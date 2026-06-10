"""casparser NSDL/CDSL eCAS wrapper. Mapping tested in-memory."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import casparser
import pytest
from casparser.enums import FileType
from casparser.exceptions import IncorrectPasswordError
from casparser.types import (
    Bond,
    DematAccount,
    DematOwner,
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
    owners: list[DematOwner] | None = None,
) -> DematAccount:
    return DematAccount(
        name=broker,
        type="PRIMARY",
        dp_id="IN300000",
        client_id=client_id,
        folios=0,  # casparser: folio *count*, not a list
        balance=Decimal("0"),
        owners=owners or [],
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


def test_mutual_fund_folios_section_is_tagged_not_demat():
    """casparser emits the RTA "Mutual Fund Folios" section as a synthetic account
    block (type="Mutual Fund Folios"); it must map with kind="mf_folios" so the
    importer's "Demat accounts" count excludes it."""
    mf_section = DematAccount(
        name="Mutual Fund Folios",
        type="Mutual Fund Folios",
        dp_id="",
        client_id="",
        folios=1,
        balance=Decimal("0"),
        owners=[],
        equities=[],
        mutual_funds=[_mf("Equity Fund", "INF109K01VQ4", "50", "120", "100")],
        bonds=[],
    )
    cas = _ecas([_account("ZERODHA", "1208160001234567"), mf_section])

    stmt = ecas_parser.map_ecas_data(cas)

    assert [a.kind for a in stmt.accounts] == ["demat", "mf_folios"]


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


def _owner(name: str, pan: str) -> DematOwner:
    return DematOwner(name=name, PAN=pan)


def test_ecas_investor_identity_uses_primary_owner_pan():
    cas = _ecas([_account("Zerodha", "C1", owners=[_owner("RAHUL SHARMA", "ABCDE1234F")])])
    identity = ecas_parser.ecas_investor_identity(cas)
    assert identity.pan == "ABCDE1234F"
    assert identity.name == "Sample"
    assert identity.email == "s@example.com"


def test_ecas_investor_identity_empty_when_no_owner_pan():
    cas = _ecas([_account("Zerodha", "C1", owners=[])])
    assert ecas_parser.ecas_investor_identity(cas).pan == ""


def test_ecas_investor_identity_rejects_statement_spanning_multiple_pans():
    cas = _ecas(
        [
            _account("Zerodha", "C1", owners=[_owner("A", "ABCDE1234F")]),
            _account("Groww", "C2", owners=[_owner("B", "ZZZZZ9999Z")]),
        ]
    )
    with pytest.raises(ecas_parser.CASParseError, match="more than one PAN") as exc:
        ecas_parser.ecas_investor_identity(cas)
    # PII-free: neither PAN appears in the message.
    assert "ABCDE1234F" not in str(exc.value)
    assert "ZZZZZ9999Z" not in str(exc.value)


def test_equity_holding_carries_symbol_and_exchange():
    # casparser backfills symbol/exchange onto demat equities from the ISIN DB;
    # the mapper must carry them onto the core Security so it's priceable. The
    # mapper takes a duck-typed object, so a stub keeps this independent of the
    # installed casparser Equity's field set.
    eq = SimpleNamespace(
        name="Reliance Industries",
        isin="INE002A01018",
        num_shares=Decimal("10"),
        value=Decimal("14000"),
        symbol="RELIANCE",
        exchange="NSE",
    )
    line = ecas_parser._map_equity_holding(eq)
    assert line.security.type is SecurityType.EQUITY
    assert line.security.symbol == "RELIANCE"
    assert line.security.exchange == "NSE"


def test_equity_holding_without_symbol_maps_empty():
    # An eCAS equity the ISIN DB couldn't map (or an older casparser with no
    # symbol field) stays symbol-less rather than crashing.
    eq = SimpleNamespace(
        name="Unlisted Co", isin="INE000X00X00", num_shares=Decimal("5"), value=Decimal("50")
    )
    line = ecas_parser._map_equity_holding(eq)
    assert line.security.symbol == ""
    assert line.security.exchange == ""
