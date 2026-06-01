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
from casparser.exceptions import IncorrectPasswordError, ParserException
from casparser.types import NSDLCASData

from folioman_core._dates import parse_loose_date as _to_date
from folioman_core.models.cas import (
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
    # Equity: no symbol in casparser model; ISIN identifies the security.
    return EcasHoldingLine(
        security=Security(
            type=SecurityType.EQUITY, name=eq.name, isin=eq.isin or "", currency="INR"
        ),
        units=eq.num_shares,
        value_observed=eq.value,
        avg_cost_observed=None,
    )


def _map_mf_holding(mf: object) -> EcasHoldingLine:
    # Demat-held MF — equity-orientation unknown from eCAS; left unflagged (default
    # is non-equity), so reconciliation against an MF CAS (which sets the flag from
    # FundType) is what enables 112A treatment.
    #
    # The eCAS prints the RTA folio per MF holding — the *same* identity as the MF
    # CAS ledger. Carry it (as an MF folio) so the holding reconciles against the
    # ledger instead of landing under the demat account number and double-counting.
    rta_folio = str(getattr(mf, "folio", "") or "").strip()
    folio = Folio(folio_type=FolioType.MF, number=rta_folio[:64]) if rta_folio else None
    return EcasHoldingLine(
        security=Security(type=SecurityType.MF, name=mf.name, isin=mf.isin or "", currency="INR"),
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
    holdings: list[EcasHoldingLine] = []
    holdings.extend(_map_equity_holding(eq) for eq in account.equities)
    holdings.extend(_map_mf_holding(mf) for mf in account.mutual_funds)
    holdings.extend(_map_bond_holding(b) for b in account.bonds)
    return EcasAccountBlock(folio=folio, holdings=holdings)


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
