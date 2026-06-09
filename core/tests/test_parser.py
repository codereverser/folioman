"""casparser MF CAS wrapper. Mapping tested in-memory (no PDF)."""

from datetime import date
from decimal import Decimal

import casparser
import pytest
from casparser.enums import CASFileType, FileType, FundType
from casparser.enums import TransactionType as CTxn
from casparser.exceptions import IncorrectPasswordError
from casparser.types import (
    CASData,
    Folio,
    InvestorInfo,
    Scheme,
    SchemeValuation,
    StatementPeriod,
    TransactionData,
)
from folioman_core import parser
from folioman_core.models import SecurityType, TransactionType


def _txn(d: date, typ: CTxn, units: str, nav: str, amount: str) -> TransactionData:
    return TransactionData(
        date=d,
        description=typ.value,
        amount=Decimal(amount),
        units=Decimal(units),
        nav=Decimal(nav),
        balance=Decimal("0"),
        type=typ,
        dividend_rate=None,
    )


def _cas(
    transactions: list[TransactionData],
    *,
    fund_type: FundType = FundType.EQUITY,
    open_units: str = "0",
    close_units: str = "40",
) -> CASData:
    val = SchemeValuation(
        date=date(2025, 3, 31), nav=Decimal("25"), cost=Decimal("400"), value=Decimal("1000")
    )
    scheme = Scheme(
        scheme="Test Equity Fund",
        advisor="",
        rta_code="X",
        rta="CAMS",
        type=fund_type,
        isin="INF109K01VQ4",
        amfi="122639",
        nominees=[],
        open=Decimal(open_units),
        close=Decimal(close_units),
        close_calculated=Decimal(close_units),
        valuation=val,
        transactions=transactions,
    )
    folio = Folio(
        folio="12345/67", amc="Test MF", PAN="ABCDE1234F", KYC="OK", PANKYC="OK", schemes=[scheme]
    )
    return CASData(
        statement_period=StatementPeriod(from_="2022-01-01", to="2025-03-31"),
        folios=[folio],
        investor_info=InvestorInfo(name="Sample", email="s@example.com", address="a", mobile="9"),
        cas_type=CASFileType.DETAILED,
        file_type=FileType.CAMS,
    )


def _cas_named(scheme_name: str, *, isin: str | None, amfi: str | None) -> CASData:
    """A single-scheme CAS with a custom name/isin/amfi, for mapping-failure tests."""
    val = SchemeValuation(
        date=date(2025, 3, 31), nav=Decimal("25"), cost=Decimal("400"), value=Decimal("1000")
    )
    scheme = Scheme(
        scheme=scheme_name,
        advisor="",
        rta_code="X",
        rta="CAMS",
        type=FundType.EQUITY,
        isin=isin,
        amfi=amfi,
        nominees=[],
        open=Decimal("0"),
        close=Decimal("0"),
        close_calculated=Decimal("0"),
        valuation=val,
        transactions=[_txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000")],
    )
    folio = Folio(
        folio="12345/67", amc="Test MF", PAN="ABCDE1234F", KYC="OK", PANKYC="OK", schemes=[scheme]
    )
    return CASData(
        statement_period=StatementPeriod(from_="2022-01-01", to="2025-03-31"),
        folios=[folio],
        investor_info=InvestorInfo(name="Sample", email="s@example.com", address="a", mobile="9"),
        cas_type=CASFileType.DETAILED,
        file_type=FileType.CAMS,
    )


def test_overlong_scheme_name_rejects_statement_without_leaking_name():
    # casparser sometimes appends footnote text to a scheme name past 255 chars.
    secret = "SECRETHOLDINGNAME"
    cas = _cas_named(secret + "x" * 300, isin="INF109K01VQ4", amfi="122639")
    with pytest.raises(parser.CASParseError) as ei:
        parser.map_cas_data(cas)
    msg = str(ei.value)
    assert "scheme 1 of folio 1" in msg
    assert "255" in msg  # the rule that failed
    assert secret not in msg  # the offending value (a holding name) is never echoed


def test_scheme_without_identifier_rejects_whole_statement():
    secret = "PRIVATEFUNDLABEL"  # invented; never echoed back in the error
    cas = _cas_named(secret, isin=None, amfi=None)
    with pytest.raises(parser.CASParseError) as ei:
        parser.map_cas_data(cas)
    msg = str(ei.value)
    assert "scheme 1 of folio 1" in msg
    assert "amfi" in msg or "isin" in msg  # names the missing-identifier rule
    assert secret not in msg  # no PII from the scheme name


def test_map_cas_data_structure_and_equity_flag():
    cas = _cas([_txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000")])
    stmt = parser.map_cas_data(cas)
    assert stmt.pan_masked == "XXXXXX234F"  # last 4 kept
    assert stmt.statement_from == date(2022, 1, 1)
    assert len(stmt.schemes) == 1
    block = stmt.schemes[0]
    assert block.security.type is SecurityType.MF
    assert block.security.isin == "INF109K01VQ4"
    assert block.security.amfi_code == "122639"
    assert block.security.metadata["equity_oriented"] is True
    assert block.folio.number == "12345/67"
    assert block.opening_units == Decimal("0")
    assert block.closing_units == Decimal("40")
    assert block.transactions[0].transaction_type is TransactionType.BUY


def test_debt_fund_not_flagged_equity_oriented():
    cas = _cas(
        [_txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000")], fund_type=FundType.DEBT
    )
    block = parser.map_cas_data(cas).schemes[0]
    assert block.security.metadata["equity_oriented"] is False


def test_sale_units_normalized_to_positive():
    # casparser signs redemptions negative; folioman keeps magnitude + direction-by-type
    cas = _cas([_txn(date(2024, 8, 1), CTxn.REDEMPTION, "-60", "25", "-1500")])
    line = parser.map_cas_data(cas).schemes[0].transactions[0]
    assert line.transaction_type is TransactionType.SELL
    assert line.units == Decimal("60")


def test_charge_rows_skipped():
    cas = _cas(
        [
            _txn(date(2024, 8, 1), CTxn.PURCHASE, "10", "10", "100"),
            _txn(date(2024, 8, 1), CTxn.STT_TAX, "0", "0", "1"),
            _txn(date(2024, 8, 1), CTxn.STAMP_DUTY_TAX, "0", "0", "1"),
        ]
    )
    lines = parser.map_cas_data(cas).schemes[0].transactions
    assert len(lines) == 1  # only the purchase survives


def test_unsupported_transaction_is_marked_unsupported():
    cas = _cas([_txn(date(2024, 8, 1), CTxn.REVERSAL, "10", "10", "100")])
    stmt = parser.map_cas_data(cas)
    block = stmt.schemes[0]

    assert block.unsupported_transaction is True
    assert block.transactions == []


def test_every_casparser_txn_type_is_categorised():
    covered = parser._BUY_TXNS | parser._SELL_TXNS | parser._DIVIDEND_TXNS
    covered |= parser._SKIP_TXNS | parser._UNSUPPORTED_TXNS
    assert covered == set(CTxn), f"uncategorised casparser txn types: {set(CTxn) - covered}"


def test_transactions_from_statement_are_valid_ledger_rows():
    cas = _cas(
        [
            _txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000"),
            _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-60", "25", "-1500"),
        ]
    )
    txns = parser.transactions_from_mf_statement(parser.map_cas_data(cas))
    assert [t.type for t in txns] == [TransactionType.BUY, TransactionType.SELL]
    assert txns[1].units == Decimal("60")
    assert txns[0].folio_number == "12345/67"


def test_scheme_has_full_history_when_open_zero_and_reconciles():
    # open 0, buy 100, sell 60 -> net 40 == close 40 -> complete history.
    cas = _cas(
        [
            _txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000"),
            _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-60", "25", "-1500"),
        ],
        open_units="0",
        close_units="40",
    )
    block = parser.map_cas_data(cas).schemes[0]
    assert block.opening_units == Decimal("0")
    assert parser.scheme_has_full_history(block) is True


def test_scheme_not_full_history_when_opening_balance_nonzero():
    # open 10 means units existed before the statement window -> incomplete.
    cas = _cas(
        [_txn(date(2024, 8, 1), CTxn.PURCHASE, "30", "10", "300")],
        open_units="10",
        close_units="40",
    )
    block = parser.map_cas_data(cas).schemes[0]
    assert block.opening_units == Decimal("10")
    assert parser.scheme_has_full_history(block) is False


def test_scheme_not_full_history_when_rows_do_not_reconcile_to_close():
    # open 0 but listed rows net to 30, not the reported close of 40 -> rows missing.
    cas = _cas(
        [_txn(date(2022, 1, 1), CTxn.PURCHASE, "30", "10", "300")],
        open_units="0",
        close_units="40",
    )
    block = parser.map_cas_data(cas).schemes[0]
    assert parser.scheme_has_full_history(block) is False


def test_scheme_chains_onto_matching_prior_ledger_balance():
    # A top-up statement: opens at 50 (matching the existing ledger), buys 20 -> 70.
    cas = _cas(
        [_txn(date(2023, 1, 1), CTxn.PURCHASE, "20", "10", "200")],
        open_units="50",
        close_units="70",
    )
    block = parser.map_cas_data(cas).schemes[0]
    # Chains cleanly when the prior ledger holds 50; a partial-period gap otherwise.
    assert parser.scheme_history_gap(block, prior_balance=Decimal("50")) is None
    assert parser.scheme_history_gap(block, prior_balance=Decimal("0")) == "opening_nonzero"


def test_scheme_history_gap_when_opening_does_not_match_prior_ledger():
    # Statement opens at 0, but the existing ledger holds 50 -> the statements
    # don't chain (a sale between them is missing).
    cas = _cas(
        [_txn(date(2024, 1, 1), CTxn.PURCHASE, "30", "10", "300")],
        open_units="0",
        close_units="30",
    )
    block = parser.map_cas_data(cas).schemes[0]
    assert parser.scheme_history_gap(block, prior_balance=Decimal("50")) == "history_gap"


def test_read_mf_cas_wraps_wrong_password(monkeypatch):
    def boom(*_args, **_kwargs):
        raise IncorrectPasswordError("bad password")

    monkeypatch.setattr(casparser, "read_cas_pdf", boom)
    with pytest.raises(parser.CASPasswordError):
        parser.read_mf_cas("whatever.pdf", "wrong")


def test_read_mf_cas_rejects_non_mf_cas(monkeypatch):
    sentinel = object()  # not a CASData instance -> treated as unsupported shape
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda *a, **k: sentinel)
    with pytest.raises(parser.CASParseError, match="ecas_parser"):
        parser.read_mf_cas("statement.pdf", "pw")


def test_mf_investor_identity_carries_full_pan_name_email():
    # The statement view masks the PAN; the identity must carry the *full* PAN
    # (plus name/email) so the import layer can resolve/create the investor.
    cas = _cas([_txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000")])
    identity = parser.mf_investor_identity(cas)
    assert identity.pan == "ABCDE1234F"
    assert identity.name == "Sample"
    assert identity.email == "s@example.com"
    # The persisted statement view still only carries the masked PAN (last 4 kept).
    masked = parser.map_cas_data(cas).pan_masked
    assert masked.endswith("234F")
    assert masked != "ABCDE1234F"
