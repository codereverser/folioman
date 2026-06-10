"""Thin wrapper over casparser 1.0 for NSDL / CDSL eCAS (demat) PDFs.

Maps casparser's ``NSDLCASData`` into folioman's ``EcasStatement`` view and into
concrete ``Holding`` snapshot rows. CAMS/KFin MF CAS is handled by ``parser.py``.

eCAS supplies *current holdings* (no transaction history) across one or more
demat accounts (CDSL multi-account is the common case), covering equities,
demat-held mutual funds, and bonds.
"""

from __future__ import annotations

import io

import casparser
from casparser.enums import FundType
from casparser.exceptions import IncorrectPasswordError, ParserException
from casparser.types import NSDLCASData

from folioman_core._dates import parse_loose_date as _to_date
from folioman_core.models.cas import (
    CasInvestorIdentity,
    Depository,
    EcasAccountBlock,
    EcasHoldingLine,
    EcasStatement,
)
from folioman_core.models.holding import Holding, HoldingSource
from folioman_core.models.investor import Folio, FolioType
from folioman_core.models.security import Security, SecurityType
from folioman_core.parser import CASParseError, CASPasswordError


def _depository_from_file_type(ft: object) -> Depository:
    value = getattr(ft, "value", ft)
    if value == "NSDL":
        return Depository.NSDL
    if value == "CDSL":
        return Depository.CDSL
    msg = f"unexpected eCAS file_type {value!r}; expected NSDL or CDSL"
    raise CASParseError(msg)


def _map_equity_holding(eq: object) -> EcasHoldingLine:
    # eCAS prints equities by ISIN only; casparser backfills the exchange trading
    # symbol from the ISIN database (casparser.parsers._isin.batch_equity_symbols).
    # Carry it so the holding is priceable via a symbol-keyed feed (NSE / Yahoo).
    # ``getattr`` keeps this safe against an older casparser without the field.
    return EcasHoldingLine(
        security=Security(
            type=SecurityType.EQUITY,
            name=eq.name,
            isin=eq.isin or "",
            symbol=(getattr(eq, "symbol", None) or ""),
            exchange=(getattr(eq, "exchange", None) or ""),
            currency="INR",
        ),
        units=eq.num_shares,
        value_observed=eq.value,
        avg_cost_observed=None,
    )


def _map_mf_holding(mf: object) -> EcasHoldingLine:
    # casparser (v1) backfills the AMFI code + scheme type onto demat MF holdings
    # from the ISIN database (eCAS PDFs themselves carry only the ISIN). Carry both
    # so an eCAS-only fund (a) is priceable via the AMFI-code NAV feed without ever
    # needing a CAS, and (b) lines up by amfi_code/isin with the same scheme from an
    # RTA CAS. When the type is known we also flag 112A equity-orientation; left
    # unflagged when the ISIN DB has no classification (default non-equity), as
    # before — a later MF CAS still refines it.
    #
    # The eCAS prints the RTA folio per MF holding — the *same* identity as the MF
    # CAS ledger. Carry it (as an MF folio) so the holding reconciles against the
    # ledger instead of landing under the demat account number and double-counting.
    rta_folio = str(getattr(mf, "folio", "") or "").strip()
    folio = Folio(folio_type=FolioType.MF, number=rta_folio[:64]) if rta_folio else None
    fund_type = (getattr(mf, "type", None) or "").strip()
    metadata: dict[str, object] = {}
    if fund_type:
        metadata = {"equity_oriented": fund_type == FundType.EQUITY.value, "fund_type": fund_type}
    return EcasHoldingLine(
        security=Security(
            type=SecurityType.MF,
            name=mf.name or "",
            isin=mf.isin or "",
            amfi_code=(getattr(mf, "amfi", None) or ""),
            metadata=metadata,
            currency="INR",
        ),
        units=mf.balance,
        value_observed=mf.value,
        avg_cost_observed=mf.avg_cost,
        folio=folio,
    )


def _map_bond_holding(bond: object) -> EcasHoldingLine:
    return EcasHoldingLine(
        security=Security(
            type=SecurityType.BOND, name=bond.name, isin=bond.isin or "", currency="INR"
        ),
        units=bond.num_bonds,
        value_observed=bond.value,
        avg_cost_observed=None,
    )


def _map_account(account: object) -> EcasAccountBlock:
    # eCAS accounts occasionally arrive with no client_id/dp_id (and rarely no
    # name). Folio requires non-empty number + broker for DEMAT — fall back to
    # a synthetic "UNKNOWN" rather than dropping the holdings on the floor.
    number = (
        str(account.client_id or "").strip()
        or str(account.dp_id or "").strip()
        or str(account.name or "").strip()
        or "UNKNOWN"
    )
    broker = str(account.name or "").strip() or "UNKNOWN"
    folio = Folio(folio_type=FolioType.DEMAT, number=number[:64], broker=broker[:64])
    # casparser emits the statement's RTA-held "Mutual Fund Folios" section as a
    # synthetic account block with this literal type (both CDSL and NSDL). It is
    # not a demat account — tag it so counts and folio handling can differ.
    kind = (
        "mf_folios"
        if str(getattr(account, "type", "")).strip() == "Mutual Fund Folios"
        else "demat"
    )
    holdings: list[EcasHoldingLine] = []
    holdings.extend(_map_equity_holding(eq) for eq in account.equities)
    holdings.extend(_map_mf_holding(mf) for mf in account.mutual_funds)
    holdings.extend(_map_bond_holding(b) for b in account.bonds)
    return EcasAccountBlock(folio=folio, kind=kind, holdings=holdings)


def map_ecas_data(data: NSDLCASData) -> EcasStatement:
    """Map a parsed NSDL/CDSL ``NSDLCASData`` into a folioman ``EcasStatement`` view."""
    period = data.statement_period
    statement_date = _to_date(period.to)
    if statement_date is None:
        msg = f"unparseable statement_period.to: {period.to!r}"
        raise CASParseError(msg)
    info = data.investor_info
    return EcasStatement(
        depository=_depository_from_file_type(data.file_type),
        statement_date=statement_date,
        investor_name=(info.name if info else "") or "",
        accounts=[_map_account(acc) for acc in data.accounts],
    )


def ecas_investor_identity(data: NSDLCASData) -> CasInvestorIdentity:
    """Owner identity (name, email, **full** PAN) from an eCAS, for investor
    resolution. The PAN is the **primary** holder — the first owner of the first
    demat account. A consolidated eCAS for one person shares that PAN across
    accounts; if accounts disagree on the primary PAN the statement spans more
    than one taxpayer, which we reject rather than guess (message is PII-free —
    carries no PAN). ``pan == ""`` if no owner PAN is present.
    """
    primary = ""
    for account in data.accounts:
        owners = getattr(account, "owners", None) or []
        if not owners:
            continue
        pan = (getattr(owners[0], "PAN", "") or "").strip()
        if not pan:
            continue
        if not primary:
            primary = pan
        elif pan != primary:
            msg = (
                "this statement spans more than one PAN; import one holder's "
                "statement at a time (no PAN is included in this message)."
            )
            raise CASParseError(msg)
    info = data.investor_info
    return CasInvestorIdentity(
        name=(info.name if info else "") or "",
        email=(info.email if info else "") or "",
        pan=primary,
    )


def read_ecas(file: str | io.IOBase, password: str) -> EcasStatement:
    """Parse an NSDL/CDSL eCAS PDF into a folioman ``EcasStatement``.

    Raises ``CASPasswordError`` for wrong password, ``CASParseError`` for any
    other failure or when the file is a CAMS/KFin MF CAS (use ``parser.read_mf_cas``).
    """
    try:
        data = casparser.read_cas_pdf(file, password, output="dict")
    except IncorrectPasswordError as exc:
        raise CASPasswordError(str(exc)) from exc
    except ParserException as exc:
        raise CASParseError(str(exc)) from exc
    if not isinstance(data, NSDLCASData):
        ft = getattr(data, "file_type", "?")
        msg = f"not an NSDL/CDSL eCAS (file_type={ft}); use parser.read_mf_cas for CAMS/KFin"
        raise CASParseError(msg)
    return map_ecas_data(data)


def holdings_from_ecas_statement(
    stmt: EcasStatement,
    *,
    source: HoldingSource = HoldingSource.ECAS,
) -> list[Holding]:
    """Flatten an eCAS view into folioman ledger ``Holding`` records.

    A consolidated CAS spans both depositories, so all rows share the single
    ``ECAS`` source regardless of the issuing depository.
    """
    rows: list[Holding] = []
    for account in stmt.accounts:
        for line in account.holdings:
            folio = line.folio or account.folio  # MF lines carry their own RTA folio
            rows.append(
                Holding(
                    security=line.security,
                    as_of_date=stmt.statement_date,
                    units=line.units,
                    value_observed=line.value_observed,
                    avg_cost_observed=line.avg_cost_observed,
                    source=source,
                    folio_number=folio.number,
                    broker=folio.broker,
                )
            )
    return rows
