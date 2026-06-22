"""Corporate-action helpers that emit ledger rows for FIFO and reconciliation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.fifo import net_units_from_transactions
from folioman_core.models.security import Security, SecurityType
from folioman_core.models.transaction import Transaction, TransactionSource, TransactionType

_ZERO = Decimal("0")
# Tradebook / corp-action feeds are reliable for cost basis from this date onward.
COST_BASIS_RELIABLE_SINCE = date(2016, 1, 1)

# Exchange-traded instruments are held in whole units: a bonus issues integer
# shares and any sub-share entitlement is dropped (settled in cash by the registrar),
# never carried as a fractional holding. Mutual-fund units, by contrast, are genuinely
# fractional, so their multiplier applies as-is.
_WHOLE_SHARE_TYPES = frozenset({SecurityType.EQUITY, SecurityType.ETF, SecurityType.FOREIGN_EQUITY})


def _issues_whole_shares(security: Security) -> bool:
    return security.type in _WHOLE_SHARE_TYPES


def _lot_source_key(txn: Transaction) -> int | tuple:
    """Stable open-lot identity — survives object copies when ``ledger_id`` is set."""
    if txn.ledger_id is not None:
        return txn.ledger_id
    return (
        txn.date,
        txn.type,
        txn.source_ref or "",
        txn.units,
        txn.nav_or_price,
    )


def _preserved_cost_total(txn: Transaction) -> Decimal:
    """The lot's exact cost of acquisition to carry through a unit-scaling rewrite.

    A split/merger rewrites ``units`` and per-unit ``nav_or_price``; for an
    indivisible ratio the per-unit is a repeating decimal, so the lot's TOTAL cost
    is the only exact carrier. Reuse an already-preserved total when a prior CA set
    one (chained events), else the buy's ``units * nav_or_price + brokerage``.
    """
    if txn.cost_total is not None:
        return txn.cost_total
    return txn.units * txn.nav_or_price + txn.brokerage


_EVENT_ORDER: dict[CorpActionType, int] = {
    CorpActionType.SPLIT: 0,
    CorpActionType.MERGER: 1,
    CorpActionType.DEMERGER: 2,
    CorpActionType.BONUS: 3,
    CorpActionType.DIVIDEND: 4,
    CorpActionType.RIGHTS: 5,
    CorpActionType.BUYBACK: 6,
}


@dataclass(frozen=True, slots=True)
class CorporateActionApplyEvent:
    """One corporate-action to apply in chronological order with others."""

    kind: CorpActionType
    ex_date: date
    security: Security
    unit_multiplier: Decimal | None = None
    # Exact bonus ratio (a, b) for an "a:b" issue — a new shares per b held. Lets a
    # whole-share bonus issue integer shares without the rounding drift a truncated
    # decimal multiplier introduces (3 * 0.333333 floors to 0, not 1).
    bonus_ratio: tuple[int, int] | None = None
    merger_old_security: Security | None = None
    merger_new_security: Security | None = None
    merger_ratio: Decimal | None = None
    dividend_per_share: Decimal | None = None
    rights_units: Decimal | None = None
    rights_price: Decimal | None = None
    source_ref: str = ""


# Same-date ordering: acquisitions settle before disposals so FIFO never sees a
# sell ahead of its buy. source_ref is only a final, stable tiebreaker.
_TYPE_ORDER: dict[TransactionType, int] = {
    TransactionType.SPLIT: 0,
    TransactionType.MERGER: 1,
    TransactionType.BONUS: 2,
    TransactionType.TRANSFER_IN: 3,
    TransactionType.BUY: 4,
    TransactionType.DIVIDEND: 5,
    TransactionType.SELL: 6,
    TransactionType.TRANSFER_OUT: 7,
}


def _sort_transactions(transactions: list[Transaction]) -> list[Transaction]:
    return sorted(transactions, key=lambda row: (row.date, _TYPE_ORDER[row.type], row.source_ref))


def _event_sort_key(event: CorporateActionApplyEvent) -> tuple:
    return (event.ex_date, _EVENT_ORDER.get(event.kind, 99), event.source_ref)


def _same_security(left: Security, right: Security) -> bool:
    """Identity match for apply passes — ISIN when both sides have one."""
    if left.isin and right.isin:
        return left.isin == right.isin
    return left == right


def held_units_asof(
    transactions: Sequence[Transaction],
    security: Security,
    as_of: date,
    *,
    folio_number: str = "",
) -> Decimal:
    """Net units held strictly before ``as_of`` (ex-date entitlement)."""
    subset = [
        txn
        for txn in transactions
        if _same_security(txn.security, security)
        and (not folio_number or txn.folio_number == folio_number)
        and txn.date < as_of
    ]
    return net_units_from_transactions(subset)


def _stable_source_ref(core: Transaction) -> str:
    """Fallback provenance key when the event left ``source_ref`` blank."""
    ident = core.security.isin or core.security.symbol or core.security.name
    return f"ca:{core.type.value}:{core.date}:{ident}"


def _copy(txn: Transaction, **updates) -> Transaction:
    """``model_copy`` that keeps ``ledger_id`` unless explicitly overridden."""
    if "ledger_id" not in updates and txn.ledger_id is not None:
        updates = {**updates, "ledger_id": txn.ledger_id}
    return txn.model_copy(update=updates)


def cost_basis_complete_for_acquisition(acquired_on: date) -> bool:
    """Whether cost basis from this acquisition date is in the reliable window."""
    return acquired_on >= COST_BASIS_RELIABLE_SINCE


def apply_reverse_split(
    transactions: list[Transaction],
    *,
    ratio: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Apply a reverse split (``ratio`` new per old, ``ratio < 1``) with integer shares.

    Historical rows scale like a forward split; any fractional entitlement left on the
    ex-date is removed via a zero-proceeds disposal so demat balances stay whole.
    """
    if ratio <= _ZERO or ratio >= 1:
        msg = "reverse split ratio must be positive and less than 1"
        raise ValueError(msg)
    ref = source_ref or ""
    if ref and any(txn.source_ref == ref for txn in transactions):
        # Idempotent re-apply: the scaled rows + fractional disposal already carry
        # this ref, so re-running would double both. Return unchanged.
        return _sort_transactions(list(transactions))

    txns = apply_split(
        transactions,
        ratio=ratio,
        effective_date=effective_date,
        security=security,
        source_ref=source_ref,
    )
    scaled_net = net_units_from_transactions(
        [
            txn
            for txn in txns
            if _same_security(txn.security, security)
            and txn.date < effective_date
            and txn.type
            in (
                TransactionType.BUY,
                TransactionType.SELL,
                TransactionType.BONUS,
                TransactionType.TRANSFER_IN,
                TransactionType.TRANSFER_OUT,
            )
        ]
    )
    integer_net = scaled_net.to_integral_value(rounding=ROUND_FLOOR)
    fractional = scaled_net - integer_net
    if fractional <= _ZERO:
        return txns

    fractional_sell = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.SELL,
        units=fractional,
        nav_or_price=_ZERO,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref or f"reverse-split:fraction:{ratio}",
    )
    return _sort_transactions([*txns, fractional_sell])


def apply_split(
    transactions: list[Transaction],
    *,
    ratio: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Apply a ``ratio``-for-1 split (``ratio=2`` → 1:2) to history before ``effective_date``."""
    if ratio <= _ZERO:
        msg = "split ratio must be positive"
        raise ValueError(msg)
    ref = source_ref or ""
    if ref and any(txn.source_ref == ref for txn in transactions):
        # Idempotent re-apply: this split's marker is already in the ledger, so the
        # rows are scaled. Re-scaling (e.g. a reconciliation replay over an applied
        # ledger) would double the factor — return unchanged.
        return _sort_transactions(list(transactions))

    adjusted: list[Transaction] = []
    for txn in transactions:
        if txn.date >= effective_date or not _same_security(txn.security, security):
            adjusted.append(txn)
            continue
        if txn.type in (TransactionType.BUY, TransactionType.TRANSFER_IN):
            # Cost-bearing lot: preserve the exact total; per-unit is now display-only.
            adjusted.append(
                _copy(
                    txn,
                    units=txn.units * ratio,
                    nav_or_price=txn.nav_or_price / ratio,
                    cost_total=_preserved_cost_total(txn),
                )
            )
        elif txn.type in (
            TransactionType.SELL,
            TransactionType.BONUS,
            TransactionType.TRANSFER_OUT,
        ):
            # No cost basis to preserve (sells consume lots; bonus units are zero-cost).
            adjusted.append(
                _copy(
                    txn,
                    units=txn.units * ratio,
                    nav_or_price=txn.nav_or_price / ratio,
                )
            )
        else:
            adjusted.append(txn)

    marker = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.SPLIT,
        units=_ZERO,
        nav_or_price=_ZERO,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref or f"split:{ratio}",
    )
    adjusted.append(marker)
    return _sort_transactions(adjusted)


def apply_bonus_from_multiplier(
    transactions: list[Transaction],
    *,
    unit_multiplier: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
    ratio: tuple[int, int] | None = None,
) -> list[Transaction]:
    """Apply a bonus/split-style ``unit_multiplier`` as a zero-cost bonus issue.

    For a whole-share instrument with a known ``ratio`` (a, b), the bonus is the
    integer ``floor(held * a / b)`` — the registrar issues whole shares and drops the
    sub-share remainder. Exact integer arithmetic is essential: a truncated decimal
    multiplier (``1.333333`` for 1:3) floors ``3 * 0.333333`` to 0 instead of 1, and
    compounds drift across successive bonuses (POWERGRID's two 1:3 issues).
    """
    if unit_multiplier <= _ZERO:
        msg = "unit_multiplier must be positive"
        raise ValueError(msg)
    ref = source_ref or ""
    if ref and any(txn.source_ref == ref for txn in transactions):
        # Idempotent re-apply: a prior run already recorded this bonus row.
        return _sort_transactions(list(transactions))
    held = held_units_asof(transactions, security, effective_date)
    if held <= _ZERO:
        return _sort_transactions(list(transactions))
    if ratio is not None and _issues_whole_shares(security):
        a, b = ratio
        # Exact integer numerator (held * a), then floor the division by b.
        bonus_units = (held * Decimal(a) / Decimal(b)).to_integral_value(rounding=ROUND_FLOOR)
    else:
        bonus_units = held * (unit_multiplier - 1)
    if bonus_units <= _ZERO:
        # Holding too small to earn even one whole bonus share.
        return _sort_transactions(list(transactions))
    return apply_bonus(
        transactions,
        bonus_units=bonus_units,
        effective_date=effective_date,
        security=security,
        source_ref=source_ref,
    )


def apply_bonus(
    transactions: list[Transaction],
    *,
    bonus_units: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Record bonus shares at zero cost."""
    if bonus_units <= _ZERO:
        msg = "bonus_units must be positive"
        raise ValueError(msg)

    bonus = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.BONUS,
        units=bonus_units,
        nav_or_price=_ZERO,
        amount=_ZERO,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref,
    )
    if not bonus.source_ref:
        bonus = bonus.model_copy(update={"source_ref": _stable_source_ref(bonus)})
    return _sort_transactions([*transactions, bonus])


def apply_merger(
    transactions: list[Transaction],
    *,
    old_security: Security,
    new_security: Security,
    ratio: Decimal,
    effective_date: date | None = None,
    source_ref: str = "",
) -> list[Transaction]:
    """Convert a merged-away security's history into the acquiring security.

    Every ``old_security`` row is re-based onto ``new_security`` at ``ratio`` new
    shares per old share: ``units * ratio`` and ``nav_or_price / ratio`` so each
    lot's **cost basis is preserved**, and its **acquisition date is unchanged** --
    holding period and 31-Jan-2018 grandfathering carry to the new shares (a
    merger is tax-neutral under s.47(vii)/s.49(2); the new shares inherit the
    original cost and period). A pre-merger disposal re-bases identically, so its
    realised gain is invariant — only the scrip label changes. Other securities'
    rows pass through untouched.

    ``ratio`` is new-per-old: ``2 old → 1 new`` is ``ratio=0.5``; ``1 old → 3 new``
    is ``ratio=3``.
    """
    if ratio <= _ZERO:
        msg = "merger ratio must be positive"
        raise ValueError(msg)
    if old_security == new_security:
        msg = "merger needs distinct old and new securities"
        raise ValueError(msg)

    ref = source_ref or f"merger:{old_security.isin or old_security.symbol}"
    converted: list[Transaction] = []
    did_convert = False
    for txn in transactions:
        if not _same_security(txn.security, old_security):
            converted.append(txn)
            continue
        did_convert = True
        # Cost-bearing rows carry their exact total onto the new scrip (an
        # indivisible swap ratio makes the new per-unit a repeating decimal);
        # sells/zero-cost rows just re-base units + per-unit.
        cost_total = (
            _preserved_cost_total(txn)
            if txn.type in (TransactionType.BUY, TransactionType.TRANSFER_IN)
            else None
        )
        converted.append(
            _copy(
                txn,
                security=new_security,
                units=txn.units * ratio,
                nav_or_price=txn.nav_or_price / ratio,
                cost_total=cost_total,
                # Keep the original date/type so FIFO inherits the holding
                # period; tag provenance only where the row had none.
                source_ref=txn.source_ref or ref,
            )
        )
    # Leave a zero-unit provenance marker on the acquirer so the merger is visible as
    # a corporate action (the rebased lots otherwise look like ordinary trades). Only
    # when a conversion actually happened, so a re-apply over a merged ledger is a no-op.
    if did_convert and effective_date is not None:
        converted.append(
            Transaction(
                security=new_security,
                date=effective_date,
                type=TransactionType.MERGER,
                units=_ZERO,
                nav_or_price=_ZERO,
                amount=_ZERO,
                source=TransactionSource.CORPORATE_ACTION,
                source_ref=ref,
            )
        )
    return _sort_transactions(converted)


def apply_rights(
    transactions: list[Transaction],
    *,
    units: Decimal,
    price: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Record a rights issue as a dated buy at the issue price."""
    if units <= _ZERO or price < _ZERO:
        msg = "rights units must be positive and price non-negative"
        raise ValueError(msg)
    rights = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.BUY,
        units=units,
        nav_or_price=price,
        amount=units * price,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref or "rights",
    )
    return _sort_transactions([*transactions, rights])


def apply_buyback(
    transactions: list[Transaction],
    *,
    units: Decimal,
    price: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Record a buyback as a sell at the offer price."""
    if units <= _ZERO or price <= _ZERO:
        msg = "buyback units and price must be positive"
        raise ValueError(msg)
    buyback = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.SELL,
        units=units,
        nav_or_price=price,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref or "buyback",
    )
    return _sort_transactions([*transactions, buyback])


def apply_corporate_action_events(
    transactions: list[Transaction],
    events: Sequence[CorporateActionApplyEvent],
) -> list[Transaction]:
    """Apply corporate actions in ex-date order (split → merger → demerger → bonus → …)."""
    txns = list(transactions)
    for event in sorted(events, key=_event_sort_key):
        ref = event.source_ref
        if event.kind is CorpActionType.BONUS:
            if event.unit_multiplier is None:
                msg = "bonus event requires unit_multiplier"
                raise ValueError(msg)
            txns = apply_bonus_from_multiplier(
                txns,
                unit_multiplier=event.unit_multiplier,
                effective_date=event.ex_date,
                security=event.security,
                source_ref=ref,
                ratio=event.bonus_ratio,
            )
        elif event.kind is CorpActionType.SPLIT:
            if event.unit_multiplier is None:
                msg = "split event requires unit_multiplier"
                raise ValueError(msg)
            if event.unit_multiplier < 1:
                txns = apply_reverse_split(
                    txns,
                    ratio=event.unit_multiplier,
                    effective_date=event.ex_date,
                    security=event.security,
                    source_ref=ref,
                )
            else:
                txns = apply_split(
                    txns,
                    ratio=event.unit_multiplier,
                    effective_date=event.ex_date,
                    security=event.security,
                    source_ref=ref,
                )
        elif event.kind is CorpActionType.MERGER:
            if (
                event.merger_old_security is None
                or event.merger_new_security is None
                or event.merger_ratio is None
            ):
                msg = "merger event requires old/new securities and merger_ratio"
                raise ValueError(msg)
            txns = apply_merger(
                txns,
                old_security=event.merger_old_security,
                new_security=event.merger_new_security,
                ratio=event.merger_ratio,
                effective_date=event.ex_date,
                source_ref=ref,
            )
        elif event.kind is CorpActionType.DIVIDEND:
            # Dividend rows here are for ledger reconciliation only. The dividend
            # attribution pass writes these separately — do not enable both on one folio.
            if event.dividend_per_share is None:
                msg = "dividend event requires dividend_per_share"
                raise ValueError(msg)
            held = held_units_asof(txns, event.security, event.ex_date)
            if held > _ZERO:
                txns = _sort_transactions(
                    [
                        *txns,
                        record_dividend(
                            amount=held * event.dividend_per_share,
                            effective_date=event.ex_date,
                            security=event.security,
                            source_ref=ref,
                        ),
                    ]
                )
        elif event.kind is CorpActionType.RIGHTS:
            if event.rights_units is None or event.rights_price is None:
                msg = "rights event requires rights_units and rights_price"
                raise ValueError(msg)
            txns = apply_rights(
                txns,
                units=event.rights_units,
                price=event.rights_price,
                effective_date=event.ex_date,
                security=event.security,
                source_ref=ref,
            )
        elif event.kind is CorpActionType.BUYBACK:
            if event.rights_units is None or event.rights_price is None:
                msg = "buyback event requires rights_units (units) and rights_price"
                raise ValueError(msg)
            txns = apply_buyback(
                txns,
                units=event.rights_units,
                price=event.rights_price,
                effective_date=event.ex_date,
                security=event.security,
                source_ref=ref,
            )
        elif event.kind is CorpActionType.DEMERGER:
            # A demerger leaves the parent's units untouched — the child shares are
            # separate received lots recorded on their own rows. Its only effect is to
            # reduce the parent lots' cost basis by the cost the children carry away
            # (s.49(2C) net-worth split). That reduction targets the lots still open at
            # the ex-date, so it belongs to the FIFO pass, not this row rewrite; here the
            # event is a pure no-op that records the parent↔child link.
            pass
        else:
            msg = f"unsupported corporate action kind: {event.kind}"
            raise ValueError(msg)
    return txns


def record_dividend(
    *,
    amount: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> Transaction:
    """Create a dividend ledger row (cash only, zero units)."""
    if amount <= _ZERO:
        msg = "dividend amount must be positive"
        raise ValueError(msg)

    return Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.DIVIDEND,
        units=_ZERO,
        nav_or_price=_ZERO,
        amount=amount,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref,
    )
