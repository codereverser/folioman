"""casparser NSDL/CDSL eCAS wrapper. Mapping tested in-memory."""

from datetime import date
from decimal import Decimal

import casparser
import pytest
from casparser.enums import FileType
from casparser.exceptions import IncorrectPasswordError
from casparser.types import (
    Bond,
    DematAccount,
    Equity,
    InvestorInfo,
    MutualFund,
    NSDLCASData,
    StatementPeriod,
)
from folioman_core import ecas_parser
from folioman_core.models import Depository, HoldingSource, SecurityType


def _equity(name: str, isin: str, shares: str, price: str) -> Equity:
    return Equity(
        name=name,
        isin=isin,
        num_shares=Decimal(shares),
        price=Decimal(price),
        value=Decimal(shares) * Decimal(price),
    )


def _mf(name: str, isin: str, balance: str, nav: str, avg_cost: str) -> MutualFund:
    units = Decimal(balance)
    return MutualFund(
        name=name,
        isin=isin,
        balance=units,
        nav=Decimal(nav),
        value=units * Decimal(nav),
        avg_cost=Decimal(avg_cost),
        total_cost=units * Decimal(avg_cost),
        ucc="",
        folio="",
        pnl=Decimal("0"),
        return_=Decimal("0"),
    )


def _bond(name: str, isin: str, qty: str, market_price: str) -> Bond:
    n = Decimal(qty)
    return Bond(
        name=name,
        isin=isin,
        num_bonds=n,
        value=n * Decimal(market_price),
        face_value=Decimal("1000"),
        coupon_rate=Decimal("0"),
        coupon_frequency="",
        maturity_date="2030-01-01",
        market_price=Decimal(market_price),
    )


def _account(
    broker: str,
    client_id: str,
    *,
    equities: list[Equity] | None = None,
    mfs: list[MutualFund] | None = None,
    bonds: list[Bond] | None = None,
) -> DematAccount:
    return DematAccount(
        name=broker,
        type="PRIMARY",
        dp_id="IN300000",
        client_id=client_id,
        folios=0,  # casparser: folio *count*, not a list
        balance=Decimal("0"),
        owners=[],
        equities=equities or [],
        mutual_funds=mfs or [],
        bonds=bonds or [],
    )


def _ecas(accounts: list[DematAccount], *, file_type: FileType = FileType.CDSL) -> NSDLCASData:
    return NSDLCASData(
        accounts=accounts,
        statement_period=StatementPeriod(from_="2025-03-01", to="2025-03-15"),
        investor_info=InvestorInfo(name="Sample", email="s@example.com", address="a", mobile="9"),
        file_type=file_type,
    )


def test_cdsl_multi_account_with_mixed_holdings():
    cas = _ecas(
        [
            _account(
                "ZERODHA",
                "1208160001234567",
                equities=[_equity("Reliance", "INE002A01018", "10", "2850")],
                mfs=[_mf("Equity Fund", "INF109K01VQ4", "50", "120", "100")],
            ),
            _account(
                "HDFC SEC",
                "1100001234567890",
                equities=[_equity("Reliance", "INE002A01018", "5", "2850")],
                bonds=[_bond("Govt Bond", "INE020B01AA0", "2", "1050")],
            ),
        ]
    )
    stmt = ecas_parser.map_ecas_data(cas)
    assert stmt.depository is Depository.CDSL
    assert stmt.statement_date == date(2025, 3, 15)
    assert len(stmt.accounts) == 2

    a, b = stmt.accounts
    assert a.folio.broker == "ZERODHA"
    assert a.folio.number == "1208160001234567"
    assert {h.security.type for h in a.holdings} == {SecurityType.EQUITY, SecurityType.MF}
    assert next(h for h in a.holdings if h.security.type is SecurityType.MF).avg_cost_observed == (
        Decimal("100")
    )

    assert b.folio.broker == "HDFC SEC"
    assert {h.security.type for h in b.holdings} == {SecurityType.EQUITY, SecurityType.BOND}


def test_nsdl_depository_and_holding_source():
    cas = _ecas(
        [
            _account(
                "KOTAK SEC",
                "IN30000012345678",
                equities=[_equity("Infy", "INE009A01021", "20", "1400")],
            )
        ],
        file_type=FileType.NSDL,
    )
    stmt = ecas_parser.map_ecas_data(cas)
    assert stmt.depository is Depository.NSDL
    holdings = ecas_parser.holdings_from_ecas_statement(stmt)
    assert holdings[0].source is HoldingSource.ECAS


def test_holdings_carry_account_attribution():
    cas = _ecas(
        [
            _account(
                "ZERODHA",
                "1208160001234567",
                equities=[_equity("Reliance", "INE002A01018", "10", "2850")],
            )
        ]
    )
    holdings = ecas_parser.holdings_from_ecas_statement(ecas_parser.map_ecas_data(cas))
    assert len(holdings) == 1
    h = holdings[0]
    assert h.source is HoldingSource.ECAS
    assert h.broker == "ZERODHA"
    assert h.folio_number == "1208160001234567"
    assert h.units == Decimal("10")
    assert h.as_of_date == date(2025, 3, 15)


def test_same_security_across_accounts_is_hashable_and_groupable():
    # The reconciliation 'sum across accounts on the latest date' depends on
    # Security being a hashable value object — make sure eCAS-emitted Securities
    # for the same scrip in two demat accounts compare equal and dedupe.
    cas = _ecas(
        [
            _account("ZERODHA", "C1", equities=[_equity("Reliance", "INE002A01018", "10", "2850")]),
            _account("HDFC SEC", "C2", equities=[_equity("Reliance", "INE002A01018", "5", "2850")]),
        ]
    )
    holdings = ecas_parser.holdings_from_ecas_statement(ecas_parser.map_ecas_data(cas))
    by_sec: dict = {}
    for h in holdings:
        by_sec.setdefault(h.security, Decimal("0"))
        by_sec[h.security] += h.units
    assert len(by_sec) == 1  # both accounts collapse to one security key
    assert next(iter(by_sec.values())) == Decimal("15")


def test_read_ecas_wraps_wrong_password(monkeypatch):
    monkeypatch.setattr(
        casparser,
        "read_cas_pdf",
        lambda *a, **k: (_ for _ in ()).throw(IncorrectPasswordError("bad")),
    )
    with pytest.raises(ecas_parser.CASPasswordError):
        ecas_parser.read_ecas("dummy.pdf", "wrong")


def test_read_ecas_rejects_mf_cas(monkeypatch):
    # Anything that isn't NSDLCASData should be redirected to parser.read_mf_cas.
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda *a, **k: object())
    with pytest.raises(ecas_parser.CASParseError, match="read_mf_cas"):
        ecas_parser.read_ecas("dummy.pdf", "pw")
