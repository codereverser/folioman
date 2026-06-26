"""Thin wrapper over casparser 1.0 for MF CAS (CAMS / KFin) PDFs.

Maps casparser's ``CASData`` into folioman's neutral cas-view models. NSDL/CDSL
eCAS (``NSDLCASData``) is handled by ``ecas_parser``.

Unit convention: folioman keeps units non-negative, with direction carried by
``type`` (see ``Transaction``). casparser signs sale units negative so a running
balance accumulates, so units/amounts are taken as magnitudes here.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from decimal import Decimal

import casparser
from casparser.enums import FundType
from casparser.enums import TransactionType as CTxn
from casparser.exceptions import IncorrectPasswordError, ParserException
from casparser.types import CASData
from pydantic import ValidationError

from folioman_core._dates import parse_loose_date as _to_date
from folioman_core.models.cas import (
    CasInvestorIdentity,
    MfCasLineItem,
    MfCasSchemeBlock,
    MfCasStatement,
)
from folioman_core.models.investor import Folio, FolioType
from folioman_core.models.security import Security, SecurityType
from folioman_core.models.transaction import Transaction, TransactionSource, TransactionType


class CASParseError(ValueError):
    """casparser could not parse the document, or it is not a supported shape."""


class CASPasswordError(CASParseError):
    """Wrong or missing PDF password."""


class UnsupportedCASTransaction(CASParseError):
    """A casparser transaction type folioman does not yet handle."""


_ZERO = Decimal("0")
# A unit-bearing buy with no recoverable NAV is only safe to book at zero cost when
# it is sub-unit "dust" — an Additional-Allotment / rounding artifact. A material lot
# (>= this many units) with no NAV would mint a phantom zero-cost lot that overstates
# capital gains, so it fails loud (the scheme becomes a snapshot) instead.
_DUST_UNITS = Decimal("1")

_BUY_TXNS = frozenset(
    {
        CTxn.PURCHASE,
        CTxn.PURCHASE_SIP,
        CTxn.DIVIDEND_REINVEST,
        CTxn.SWITCH_IN,
        CTxn.SWITCH_IN_MERGER,
    }
)
_SELL_TXNS = frozenset({CTxn.REDEMPTION, CTxn.SWITCH_OUT, CTxn.SWITCH_OUT_MERGER})
_DIVIDEND_TXNS = frozenset({CTxn.DIVIDEND_PAYOUT})
# Charges / no unit impact — dropped from the ledger view (casparser surfaces
# STT and stamp duty separately in its 112A).
_SKIP_TXNS = frozenset({CTxn.STT_TAX, CTxn.STAMP_DUTY_TAX, CTxn.TDS_TAX, CTxn.MISC, CTxn.UNKNOWN})
# Unit-affecting but not a plain buy/sell. A REVERSAL voids an earlier row and
# is netted out against it before mapping (see ``_cancel_reversals``); only an
# *unpaired* reversal — one whose original isn't in this statement's window —
# reaches ``_map_line`` and fails loud (→ snapshot fallback). SEGREGATION
# (side-pocketing into a separate portfolio) stays unsupported. GIFT_IN /
# GIFT_OUT move units but carry no usable cost basis here: a gift inherits the
# donor's cost and holding period (s.49(1)), which the CAS doesn't report, and
# a gift out is not a transfer for capital gains (s.47(iii)). Mapping them to a
# buy/sell would mint a wrong lot or book a phantom gain — they need manual
# basis entry. Failing loud beats silently corrupting the unit balance / cost basis.
_UNSUPPORTED_TXNS = frozenset({CTxn.REVERSAL, CTxn.SEGREGATION, CTxn.GIFT_IN, CTxn.GIFT_OUT})
# Buy/sell rows a reversal can void (the only rows that move units 1:1).
_REVERSIBLE_TXNS = _BUY_TXNS | _SELL_TXNS
_PAIR_TOLERANCE = Decimal("0.0001")

_TYPE_MAP: dict[CTxn, TransactionType] = (
    dict.fromkeys(_BUY_TXNS, TransactionType.BUY)
    | dict.fromkeys(_SELL_TXNS, TransactionType.SELL)
    | dict.fromkeys(_DIVIDEND_TXNS, TransactionType.DIVIDEND)
)


def _mask_pan(pan: str | None) -> str:
    if not pan:
        return ""
    pan = pan.strip()
    return ("X" * (len(pan) - 4) + pan[-4:]) if len(pan) > 4 else pan


def _abs_or_none(value: Decimal | None) -> Decimal | None:
    return None if value is None else abs(value)


def _map_line(
    txn: object, idx: int, *, stt_for_idx: dict, stamp_for_idx: dict, nav_for_date: dict
) -> MfCasLineItem | None:
    ctype = txn.type
    if ctype in _UNSUPPORTED_TXNS:
        msg = f"unsupported CAS transaction type {ctype.value!r} (manual handling needed in v1)"
        raise UnsupportedCASTransaction(msg)
    if ctype in _SKIP_TXNS:
        return None
    mapped = _TYPE_MAP.get(ctype)
    if mapped is None:
        msg = f"unmapped casparser transaction type {ctype.value!r}"
        raise UnsupportedCASTransaction(msg)
    units, nav = txn.units, txn.nav
    amount_val = _abs_or_none(txn.amount)
    if mapped is TransactionType.DIVIDEND:
        # A dividend *payout* is a cash event: casparser carries no units or NAV
        # for it (both ``None``). Keep the cash ``amount`` as income and zero the
        # unit/NAV fields — FIFO treats DIVIDEND as a no-op on the lot balance.
        units = units if units is not None else _ZERO
        nav = nav if nav is not None else _ZERO
    else:
        # Recover a missing unit/NAV from the amount when the other is known
        # (e.g. a row that carries amount + NAV but no units).
        if units is None and nav is not None and amount_val is not None and nav != _ZERO:
            units = amount_val / nav
        elif nav is None and units is not None and amount_val is not None and units != _ZERO:
            nav = amount_val / units

        # A unit-bearing buy can still arrive with no NAV — e.g. an "Additional
        # Allotment" / rounding row (issue #80). Price it at the same day's purchase
        # NAV so the units reconcile against the folio balance with a sensible cost.
        if nav is None and units is not None:
            same_day_nav = nav_for_date.get(txn.date)
            if same_day_nav is not None:
                nav = same_day_nav
                if amount_val is None:
                    amount_val = units * nav

        # Still missing units or NAV. Booking a unit-bearing lot at zero cost is only
        # safe for sub-unit dust; a material lot with no recoverable NAV would mint a
        # phantom zero-cost lot that overstates gains, so fail loud (→ snapshot).
        if units is None or nav is None:
            resolved_units = units if units is not None else _ZERO
            if nav is None and abs(resolved_units) >= _DUST_UNITS:
                msg = f"{ctype.value!r} row is missing NAV — cannot build a tax lot"
                raise UnsupportedCASTransaction(msg)
            units = resolved_units
            nav = nav if nav is not None else _ZERO
            if amount_val is None:
                amount_val = units * nav

    # casparser model: stamp duty rides with the *buy* (per-lot) and is pro-rated
    # to each disposal as a transfer expense. STT rides with the *sell* and is
    # pro-rated by consumed units. Both flow into col 12 of Schedule 112A and
    # NEVER into cost basis (cost stays at units * nav / amount).
    # Pairing is positional (not by date) so multiple same-date sells each get
    # their own STT row instead of every same-date sell double-counting it.
    fees = stt_for_idx.get(idx, _ZERO) if mapped is TransactionType.SELL else _ZERO
    stamp_duty = stamp_for_idx.get(idx, _ZERO) if mapped is TransactionType.BUY else _ZERO
    return MfCasLineItem(
        date=txn.date,
        transaction_type=mapped,
        units=abs(units),
        nav=nav,
        amount=amount_val,
        fees=fees,
        stamp_duty=stamp_duty,
        description=txn.description or "",
        # Running balance is the absolute folio total after the txn (already
        # non-negative); keep as-is rather than taking the magnitude like units.
        balance=getattr(txn, "balance", None),
    )


def _to_decimal(value: object) -> Decimal | None:
    """casparser amounts arrive as Decimal or float; normalise to Decimal."""
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _scheme_valuation(
    scheme: object,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, object]:
    """The scheme's reported (nav, value, cost, date) from casparser's
    ``Scheme.valuation`` — the statement's own market value as of the statement
    date — or all-None when absent."""
    val = getattr(scheme, "valuation", None)
    if val is None:
        return None, None, None, None
    return (
        _to_decimal(getattr(val, "nav", None)),
        _to_decimal(getattr(val, "value", None)),
        _to_decimal(getattr(val, "cost", None)),
        _to_date(getattr(val, "date", None)),
    )


def _scheme_has_identifier(scheme: object) -> bool:
    """True if the scheme carries an ISIN or AMFI code — the stable keys folioman
    needs to dedup and price a security. Schemes with neither (matured/closed/
    segregated/unclaimed-redemption lines) can't be mapped and are skipped."""
    return bool((getattr(scheme, "isin", None) or "").strip()) or bool(
        (getattr(scheme, "amfi", None) or "").strip()
    )


def _map_security(scheme: object, amc: str | None) -> Security:
    # casparser stores Scheme.type as its enum *value* (a str); normalise either form.
    fund_type = getattr(scheme.type, "value", scheme.type) or "UNKNOWN"
    return Security(
        type=SecurityType.MF,
        name=scheme.scheme,
        isin=scheme.isin or "",
        amfi_code=scheme.amfi or "",
        metadata={
            "equity_oriented": fund_type == FundType.EQUITY.value,
            "fund_type": fund_type,
            "amc": amc or "",
        },
    )


def _map_folio(folio: object) -> Folio:
    return Folio(folio_type=FolioType.MF, number=str(folio.folio))


def _collect_per_index_charges(scheme_txns) -> tuple[dict, dict, dict]:
    """Pair STT/stamp rows with the *immediately preceding* sell/buy by index.

    Real CAS layout: each REDEMPTION is followed (on the same date) by its
    STT_TAX row(s); each PURCHASE is followed by its STAMP_DUTY_TAX row(s).
    Two same-date redemptions each get their own STT — date-keyed aggregation
    would double-count. Mirrors casparser's ``MergedTransaction`` pairing.
    """
    stt_for_idx: dict = {}
    stamp_for_idx: dict = {}
    nav_for_date: dict = {}
    last_sell_idx: int | None = None
    last_buy_idx: int | None = None
    for i, t in enumerate(scheme_txns):
        if t.type in _SELL_TXNS:
            last_sell_idx = i
        elif t.type in _BUY_TXNS:
            last_buy_idx = i
            if t.nav is not None and t.type in (CTxn.PURCHASE, CTxn.PURCHASE_SIP):
                nav_for_date[t.date] = t.nav
        elif (
            t.type is CTxn.STT_TAX
            and t.amount is not None
            and last_sell_idx is not None
            and scheme_txns[last_sell_idx].date == t.date
        ):
            stt_for_idx[last_sell_idx] = stt_for_idx.get(last_sell_idx, _ZERO) + abs(t.amount)
        elif (
            t.type is CTxn.STAMP_DUTY_TAX
            and t.amount is not None
            and last_buy_idx is not None
            and scheme_txns[last_buy_idx].date == t.date
        ):
            stamp_for_idx[last_buy_idx] = stamp_for_idx.get(last_buy_idx, _ZERO) + abs(t.amount)
    return stt_for_idx, stamp_for_idx, nav_for_date


def _close(a: Decimal | None, b: Decimal | None) -> bool:
    """True iff ``a ≈ b`` within tolerance. ``None`` on either side never matches."""
    if a is None or b is None:
        return False
    return abs(a - b) <= _PAIR_TOLERANCE


def _opposite(a: Decimal | None, b: Decimal | None) -> bool:
    """True iff ``a ≈ -b`` within tolerance. ``None`` on either side never matches."""
    if a is None or b is None:
        return False
    return abs(a + b) <= _PAIR_TOLERANCE


def _reversal_match(scheme_txns: Sequence, j: int, dropped: set[int]) -> int | None:
    """Index of the buy/sell that the REVERSAL at ``j`` voids, or ``None``.

    A reversal carries the **exact date and NAV** of the row it cancels — true for
    every reversal across the sample CAS corpus — so the pair key is: equal and
    opposite units, same date, same NAV. That triple is strong enough that a
    coincidental same-magnitude SIP can't be mistaken for the original (it would
    need the same date *and* NAV too). The RTA may list the reversal either before
    or after its original, so we search outward in both directions and take the
    nearest not-yet-paired match. No match (e.g. the original is in a prior
    statement window) → ``None`` → the reversal stays and the scheme is snapshotted.
    """
    rev = scheme_txns[j]
    rev_units = getattr(rev, "units", None)
    if rev_units is None:
        return None
    n = len(scheme_txns)
    for dist in range(1, n):  # widening radius: nearest match wins, ties prefer earlier
        for i in (j - dist, j + dist):
            if i < 0 or i >= n or i in dropped:
                continue
            cand = scheme_txns[i]
            if cand.type not in _REVERSIBLE_TXNS:
                continue
            if (
                _opposite(getattr(cand, "units", None), rev_units)
                and cand.date == rev.date
                and _close(getattr(cand, "nav", None), rev.nav)
            ):
                return i
    return None


def _cancel_reversals(scheme_txns: Sequence) -> list:
    """Drop each REVERSAL row together with the transaction it voids.

    A reversal undoes an earlier transaction — a bounced/returned purchase, or a
    reversed redemption — and the statement's closing balance already excludes
    it. Mapping a reversal to a SELL would book a phantom capital gain (FIFO
    would consume the wrong, oldest lot); mapping it to a BUY would mint a phantom
    lot. The tax-correct treatment is that the voided pair simply never happened,
    so we remove both rows: FIFO then replays the surviving rows with the
    original lots' cost basis and acquisition dates intact, and no spurious
    disposal.

    Non-reversal rows are returned untouched and in their original order, so the
    positional STT/stamp pairing in ``_collect_per_index_charges`` still lines up.
    An *unpaired* reversal (its original isn't in this window) is left in place so
    ``_map_line`` raises ``UnsupportedCASTransaction`` and the scheme is recorded
    as a snapshot instead of a (would-be-wrong) tax ledger.
    """
    reversal_idxs = [
        i for i, t in enumerate(scheme_txns) if getattr(t, "type", None) is CTxn.REVERSAL
    ]
    if not reversal_idxs:
        return list(scheme_txns)

    dropped: set[int] = set()
    for j in reversal_idxs:
        match = _reversal_match(scheme_txns, j, dropped)
        if match is not None:
            dropped.update((match, j))
    if not dropped:
        return list(scheme_txns)
    return [t for k, t in enumerate(scheme_txns) if k not in dropped]


def _scheme_map_reason(exc: Exception) -> str:
    """A PII-free reason for a scheme-mapping failure.

    Pydantic's ``msg``/``loc`` describe the *rule* that failed (e.g. "String
    should have at most 255 characters") without echoing the offending value;
    the raw ``input`` (which would carry the fund name) is deliberately dropped.
    """
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "field"
            parts.append(f"{loc}: {err.get('msg', 'invalid')}")
        return "; ".join(parts) or "validation error"
    return type(exc).__name__


def map_cas_data(cas: CASData) -> MfCasStatement:
    """Map a parsed CAMS/KFin ``CASData`` into a folioman ``MfCasStatement`` view.

    A scheme that can't be mapped fails the **whole** statement (rather than being
    silently skipped): one malformed scheme usually signals a casparser parsing
    artifact that may have corrupted siblings too, so the safe move is to reject
    and have the user report it. The raised ``CASParseError`` carries only
    non-PII coordinates (folio/scheme position + the rule that failed) — never the
    fund name or PAN — so it's safe to log, store on the job, and paste into a bug.
    """
    blocks: list[MfCasSchemeBlock] = []
    skipped_unidentified = 0
    for folio_pos, folio in enumerate(cas.folios, start=1):
        mapped_folio = _map_folio(folio)
        for scheme_pos, scheme in enumerate(folio.schemes, start=1):
            if not _scheme_has_identifier(scheme):
                # No ISIN and no AMFI code: a matured/closed/segregated/unclaimed
                # scheme (or a casparser artifact). We can't form a Security to
                # dedup or price it, so skip just this scheme rather than fail the
                # whole statement — a modern CAS always carries an ISIN for any
                # active holding, so this only ever drops the dormant tail.
                skipped_unidentified += 1
                continue
            try:
                # Net out REVERSAL rows against the transactions they void before
                # building the ledger (a reversal isn't a real buy or sell).
                scheme_txns = _cancel_reversals(scheme.transactions)
                stt_for_idx, stamp_for_idx, nav_for_date = _collect_per_index_charges(scheme_txns)
                lines = [
                    li
                    for li in (
                        _map_line(
                            t,
                            i,
                            stt_for_idx=stt_for_idx,
                            stamp_for_idx=stamp_for_idx,
                            nav_for_date=nav_for_date,
                        )
                        for i, t in enumerate(scheme_txns)
                    )
                    if li is not None
                ]
                val_nav, val_value, val_cost, val_date = _scheme_valuation(scheme)
                block = MfCasSchemeBlock(
                    folio=mapped_folio,
                    security=_map_security(scheme, folio.amc),
                    transactions=lines,
                    opening_units=scheme.open,
                    closing_units=scheme.close,
                    closing_nav=val_nav,
                    closing_value=val_value,
                    closing_cost=val_cost,
                    closing_value_date=val_date,
                )
            except CASParseError:
                raise  # already a clean, intentional CAS error (e.g. unsupported txn)
            except (ValidationError, ValueError) as exc:
                msg = (
                    f"could not map scheme {scheme_pos} of folio {folio_pos} "
                    f"({_scheme_map_reason(exc)}). This looks like a CAS parsing "
                    "artifact; please report it on the casparser/folioman tracker "
                    "(no fund names or PAN are included in this message)."
                )
                raise CASParseError(msg) from exc
            blocks.append(block)
    period = cas.statement_period
    info = cas.investor_info
    pan = cas.folios[0].PAN if cas.folios else None
    return MfCasStatement(
        investor_name=info.name or "",
        investor_email=info.email or "",
        pan_masked=_mask_pan(pan),
        statement_from=_to_date(period.from_),
        statement_to=_to_date(period.to),
        schemes=blocks,
        skipped_unidentified=skipped_unidentified,
    )


def mf_investor_identity(cas: CASData) -> CasInvestorIdentity:
    """Owner identity (name, email, **full** PAN) from an MF CAS, for investor
    resolution. The PAN is the folio-level PAN casparser exposes; a CAMS/KFin CAS
    is generated per PAN, so all folios share it. ``pan == ""`` if absent."""
    info = cas.investor_info
    pan = cas.folios[0].PAN if cas.folios else None
    return CasInvestorIdentity(
        name=info.name or "",
        email=info.email or "",
        pan=(pan or "").strip(),
    )


def read_mf_cas(file: str | io.IOBase, password: str) -> MfCasStatement:
    """Parse a CAMS/KFin MF CAS PDF into a folioman ``MfCasStatement``.

    Raises ``CASPasswordError`` for a wrong password, ``CASParseError`` for any
    other parse failure or when the file is an NSDL/CDSL eCAS (use ``ecas_parser``).
    """
    try:
        data = casparser.read_cas_pdf(file, password, output="dict")
    except IncorrectPasswordError as exc:
        raise CASPasswordError(str(exc)) from exc
    except ParserException as exc:
        raise CASParseError(str(exc)) from exc
    if not isinstance(data, CASData):
        ft = getattr(data, "file_type", "?")
        msg = f"not a CAMS/KFin MF CAS (file_type={ft}); use ecas_parser for NSDL/CDSL eCAS"
        raise CASParseError(msg)
    return map_cas_data(data)


_HISTORY_TOLERANCE = Decimal("0.0001")


def net_units_from_lines(lines: Iterable[MfCasLineItem]) -> Decimal:
    """Net units implied by a scheme block's listed transactions."""
    total = _ZERO
    for line in lines:
        if line.transaction_type in (
            TransactionType.BUY,
            TransactionType.BONUS,
            TransactionType.TRANSFER_IN,
        ):
            total += line.units
        elif line.transaction_type in (TransactionType.SELL, TransactionType.TRANSFER_OUT):
            total -= line.units
    return total


def scheme_history_gap(block: MfCasSchemeBlock, *, prior_balance: Decimal = _ZERO) -> str | None:
    """Why a block can't extend a gap-free ledger, or ``None`` if it can.

    ``prior_balance`` is the units already on the ledger for this (security, folio)
    *as of the statement's start* — ``0`` for the first/since-inception import. The
    caller (the importer) supplies it from the existing ledger so this stays pure.

    Reasons (distinct because they mean different things to the user):

    * ``"opening_nonzero"`` — there's no prior ledger (``prior_balance == 0``) yet
      the opening balance is non-zero: units were held before this statement's
      window and we have no record of them (a partial-period statement). Acquisition
      lots would be missing.
    * ``"history_gap"`` — there *is* a prior ledger, but the opening balance doesn't
      equal it: the statements don't chain (activity between them is missing).
    * ``"rows_unreconciled"`` — the listed rows don't carry the opening balance to
      the reported close (``opening + net != closing``) — rows missing/unmapped.
      Checked only when the close is known.

    A block with a gap must NOT be persisted as a ledger — record its closing
    balance as a holding snapshot instead (net-worth only, not tax-safe).
    """
    open_units = block.opening_units if block.opening_units is not None else _ZERO
    if abs(open_units - prior_balance) > _HISTORY_TOLERANCE:
        return "opening_nonzero" if abs(prior_balance) <= _HISTORY_TOLERANCE else "history_gap"
    if block.closing_units is not None:
        net = net_units_from_lines(block.transactions)
        if abs((open_units + net) - block.closing_units) > _HISTORY_TOLERANCE:
            return "rows_unreconciled"
    return None


def scheme_has_full_history(block: MfCasSchemeBlock, *, prior_balance: Decimal = _ZERO) -> bool:
    """True iff the block cleanly extends the existing ledger (gap-free)
    (see ``scheme_history_gap``)."""
    return scheme_history_gap(block, prior_balance=prior_balance) is None


def transactions_from_mf_statement(
    stmt: MfCasStatement,
    *,
    source: TransactionSource = TransactionSource.CAS_PDF,
) -> list[Transaction]:
    """Flatten a parsed CAS view into folioman ledger ``Transaction`` rows."""
    txns: list[Transaction] = []
    for block in stmt.schemes:
        for line in block.transactions:
            txns.append(
                Transaction(
                    security=block.security,
                    date=line.date,
                    type=line.transaction_type,
                    units=line.units,
                    nav_or_price=line.nav,
                    amount=line.amount,
                    fees=line.fees,
                    stamp_duty=line.stamp_duty,
                    source=source,
                    folio_number=block.folio.number,
                )
            )
    return txns
