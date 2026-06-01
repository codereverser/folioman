"""Cross-check folioman's capital-gains math against casparser.

casparser's ``CapitalGainsReport`` runs on the ``CASData`` object (not the PDF),
so we can validate folioman's FIFO + 112A gain against casparser's
utility-verified output entirely in memory — no real CAS PDF required.

Scope: non-grandfathered (post-31-Jan-2018 / AE) LTCG, where no 31-Jan-2018 FMV
data is needed. Grandfathered (BE) cross-checks need casparser's bundled NAV data
for a real ISIN and are covered by the manual real-CAS check noted in the plan.
"""

import csv
import io
from datetime import date
from decimal import Decimal

import casparser
from casparser.enums import CASFileType, FileType, FundType
from casparser.enums import TransactionType as CTxn
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
from folioman_core.price_feeds.casparser_fmv import fmv_lookup as _FMV
from folioman_core.reconciliation import IntegrityStatus
from folioman_core.tax import compute_gain_lines, compute_schedule_112a, get_policy
from folioman_core.tax.schedule_112a import SCHEDULE_112A_CSV_COLUMNS


def _all_reconciled(lines):
    """Tax-ready status for every security in the gain lines. These tests verify
    the tax math byte-for-byte against casparser; the 112A export fails closed on
    a missing integrity status, so supply one explicitly."""
    return {line.disposal.security: IntegrityStatus.RECONCILED for line in lines}


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


def _cas(transactions: list[TransactionData]) -> CASData:
    val = SchemeValuation(
        date=date(2025, 3, 31), nav=Decimal("25"), cost=Decimal("0"), value=Decimal("0")
    )
    scheme = Scheme(
        scheme="Test Equity Fund",
        advisor="",
        rta_code="X",
        rta="CAMS",
        type=FundType.EQUITY,
        isin="INF109K01VQ4",
        amfi="122639",
        nominees=[],
        open=Decimal("0"),
        close=Decimal("0"),
        close_calculated=Decimal("0"),
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


def _casparser_total_112a_balance(cas: CASData, casparser_fy: str) -> Decimal:
    report = casparser.CapitalGainsReport(cas)
    csv_text = report.generate_112a_csv_data(casparser_fy)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    if not rows:
        return Decimal("0")
    balance_col = next(c for c in rows[0] if c.startswith("Balance"))
    return sum((Decimal(r[balance_col]) for r in rows), Decimal("0"))


def _folioman_total_112a_balance(cas: CASData, fy_label: str) -> Decimal:
    stmt = parser.map_cas_data(cas)
    txns = parser.transactions_from_mf_statement(stmt)
    lines = compute_gain_lines(txns, get_policy("IN"), fmv_lookup=_FMV)
    rows = compute_schedule_112a(
        lines, fy_label, integrity_by_security=_all_reconciled(lines), fmv_lookup=_FMV
    )
    return sum((row.balance for row in rows), Decimal("0"))


def test_ltcg_total_matches_casparser_single_lot():
    cas = _cas(
        [
            _txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000"),
            _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-60", "25", "-1500"),
        ]
    )
    assert _folioman_total_112a_balance(cas, "2024-25") == _casparser_total_112a_balance(
        cas, "FY2024-25"
    )


def test_ltcg_total_matches_casparser_multi_lot():
    cas = _cas(
        [
            _txn(date(2021, 1, 1), CTxn.PURCHASE, "50", "10", "500"),
            _txn(date(2022, 6, 1), CTxn.PURCHASE_SIP, "50", "20", "1000"),
            _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-80", "30", "-2400"),
        ]
    )
    assert _folioman_total_112a_balance(cas, "2024-25") == _casparser_total_112a_balance(
        cas, "FY2024-25"
    )


def test_ltcg_total_matches_casparser_two_schemes_one_cas():
    """Regression: a multi-scheme ledger must not mix lots across schemes.

    Pre-fix, ``apply_fifo`` ran one global FIFO across all transactions, so a
    sell from Scheme B could pull lots from Scheme A and report nonsensical
    cost basis. The smoke test against a real CAMS CAS surfaced this; this
    in-memory case is the headless guard.
    """
    val = SchemeValuation(
        date=date(2025, 3, 31), nav=Decimal("0"), cost=Decimal("0"), value=Decimal("0")
    )

    def scheme(name: str, isin: str, amfi: str, txns: list[TransactionData]) -> Scheme:
        return Scheme(
            scheme=name,
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
            transactions=txns,
        )

    folio = Folio(
        folio="123/45",
        amc="Test MF",
        PAN="ABCDE1234F",
        KYC="OK",
        PANKYC="OK",
        schemes=[
            scheme(
                "Fund A",
                "INF109K01VQ4",
                "100001",
                [
                    _txn(date(2021, 1, 1), CTxn.PURCHASE, "1", "5", "5"),  # old stale lot
                ],
            ),
            scheme(
                "Fund B",
                "INF204KB14I2",
                "100002",
                [
                    _txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000"),
                    _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-60", "25", "-1500"),
                ],
            ),
        ],
    )
    cas = CASData(
        statement_period=StatementPeriod(from_="2021-01-01", to="2025-03-31"),
        folios=[folio],
        investor_info=InvestorInfo(name="X", email="x@y.com", address="a", mobile="9"),
        cas_type=CASFileType.DETAILED,
        file_type=FileType.CAMS,
    )
    assert _folioman_total_112a_balance(cas, "2024-25") == _casparser_total_112a_balance(
        cas, "FY2024-25"
    )


def test_csv_headers_byte_match_casparser():
    """Drift-detector: folioman's column headers must equal casparser's verbatim."""
    cas = _cas(
        [
            _txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000"),
            _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-60", "25", "-1500"),
        ]
    )
    casparser_header = (
        casparser.CapitalGainsReport(cas).generate_112a_csv_data("FY2024-25").splitlines()[0]
    )
    assert ",".join(SCHEDULE_112A_CSV_COLUMNS) == casparser_header


# ---------------------------------------------------------------------------
# Real-ISIN regression suite. Real ISINs (so casparser-isin's bundled FMV-as-of
# 31-Jan-2018 lookup actually resolves) with synthesised, PII-free transaction
# patterns covering the scenarios our minimal tests missed. Both engines must
# converge — these tests are the contract.
# ---------------------------------------------------------------------------

# Real, equity-oriented MF ISINs known to casparser-isin (verified to return FMV).
# Picked from the divergence list surfaced by the real-CAS smoke test.
_ISIN_PRE2018_A = "INF173K01155"  # FMV 111.62
_ISIN_PRE2018_B = "INF769K01BI1"  # FMV 54.687
_ISIN_PRE2018_C = "INF200K01370"  # FMV 136.4956


def _scheme(isin: str, txns: list[TransactionData], *, amfi: str = "100000") -> Scheme:
    val = SchemeValuation(
        date=date(2025, 3, 31), nav=Decimal("0"), cost=Decimal("0"), value=Decimal("0")
    )
    return Scheme(
        scheme=f"Fund {isin[-4:]}",
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
        transactions=txns,
    )


def _cas_with(schemes: list[Scheme], *, folio_id: str = "F1") -> CASData:
    folio = Folio(
        folio=folio_id, amc="AMC", PAN="ABCDE1234F", KYC="OK", PANKYC="OK", schemes=schemes
    )
    return CASData(
        statement_period=StatementPeriod(from_="2017-01-01", to="2026-03-31"),
        folios=[folio],
        investor_info=InvestorInfo(name="X", email="x@y.com", address="a", mobile="9"),
        cas_type=CASFileType.DETAILED,
        file_type=FileType.CAMS,
    )


def _stt(d: date, amount: str) -> TransactionData:
    return _txn(d, CTxn.STT_TAX, "0", "0", amount)


def _assert_matches(cas: CASData, fy_label: str, casparser_fy: str | None = None) -> None:
    casparser_fy = casparser_fy or "FY" + fy_label
    folioman = _folioman_total_112a_balance(cas, fy_label)
    cp = _casparser_total_112a_balance(cas, casparser_fy)
    assert folioman == cp, f"{fy_label}: folioman={folioman} casparser={cp} (Δ={folioman - cp})"


def test_xc_pre2018_buy_post2024_sell_uses_fmv_grandfathering():
    """A pre-31-Jan-2018 acquisition sold for LTCG must use FMV cost via Section 55(2)(ac)."""
    cas = _cas_with(
        [
            _scheme(
                _ISIN_PRE2018_A,
                [
                    _txn(date(2017, 6, 1), CTxn.PURCHASE, "100", "20", "2000"),
                    _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-100", "200", "-20000"),
                    _stt(
                        date(2024, 8, 1), "20.00"
                    ),  # casparser classifies fund EQUITY via STT presence
                ],
            )
        ]
    )
    _assert_matches(cas, "2024-25")


def test_xc_pre2018_sell_before_jul2024_cutoff_bucket_1b():
    """A sell on 22-Jul-2024 (just before the regime cutoff) lands in the BE bucket of col 1b."""
    cas = _cas_with(
        [
            _scheme(
                _ISIN_PRE2018_B,
                [
                    _txn(date(2017, 3, 1), CTxn.PURCHASE, "50", "30", "1500"),
                    _txn(date(2024, 7, 22), CTxn.REDEMPTION, "-50", "100", "-5000"),
                    _stt(date(2024, 7, 22), "5.00"),
                ],
            )
        ]
    )
    _assert_matches(cas, "2024-25")


def test_xc_mixed_pre_and_post_2018_lots_same_sell():
    """One sell consumes both a grandfathered (BE) lot and a post-2018 (AE) lot."""
    cas = _cas_with(
        [
            _scheme(
                _ISIN_PRE2018_C,
                [
                    _txn(date(2017, 9, 1), CTxn.PURCHASE, "30", "40", "1200"),
                    _txn(date(2020, 5, 1), CTxn.PURCHASE, "20", "70", "1400"),
                    _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-50", "200", "-10000"),
                    _stt(date(2024, 8, 1), "10.00"),
                ],
            )
        ]
    )
    _assert_matches(cas, "2024-25")


def test_xc_sip_pattern_and_sell():
    """Multiple SIP purchases then a partial sell — exercises multi-lot FIFO + AE consolidation."""
    cas = _cas_with(
        [
            _scheme(
                _ISIN_PRE2018_A,
                [
                    _txn(date(2022, 1, 5), CTxn.PURCHASE_SIP, "10", "50", "500"),
                    _txn(date(2022, 2, 5), CTxn.PURCHASE_SIP, "10", "55", "550"),
                    _txn(date(2022, 3, 5), CTxn.PURCHASE_SIP, "10", "60", "600"),
                    _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-15", "120", "-1800"),
                    _stt(date(2024, 8, 1), "1.80"),
                ],
            )
        ]
    )
    _assert_matches(cas, "2024-25")


def test_xc_two_schemes_one_folio_no_cross_contamination():
    """Two funds in one folio — pre-fix global FIFO mis-matched lots across schemes."""
    cas = _cas_with(
        [
            _scheme(_ISIN_PRE2018_A, [_txn(date(2021, 1, 1), CTxn.PURCHASE, "1", "5", "5")]),
            _scheme(
                _ISIN_PRE2018_B,
                [
                    _txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000"),
                    _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-60", "25", "-1500"),
                    _stt(date(2024, 8, 1), "1.50"),
                ],
            ),
        ]
    )
    _assert_matches(cas, "2024-25")


def test_xc_same_scheme_two_folios_isolated_cost_bases():
    """Same ISIN held in two folios — each folio is its own cost-basis bucket."""
    sch_old = _scheme(
        _ISIN_PRE2018_C,
        [
            _txn(date(2017, 1, 1), CTxn.PURCHASE, "10", "30", "300"),
        ],
    )
    sch_new = _scheme(
        _ISIN_PRE2018_C,
        [
            _txn(date(2022, 6, 1), CTxn.PURCHASE, "10", "200", "2000"),
            _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-10", "300", "-3000"),
            _stt(date(2024, 8, 1), "3.00"),
        ],
    )
    cas = CASData(
        statement_period=StatementPeriod(from_="2017-01-01", to="2026-03-31"),
        folios=[
            Folio(
                folio="F1", amc="AMC", PAN="ABCDE1234F", KYC="OK", PANKYC="OK", schemes=[sch_old]
            ),
            Folio(
                folio="F2", amc="AMC", PAN="ABCDE1234F", KYC="OK", PANKYC="OK", schemes=[sch_new]
            ),
        ],
        investor_info=InvestorInfo(name="X", email="x@y.com", address="a", mobile="9"),
        cas_type=CASFileType.DETAILED,
        file_type=FileType.CAMS,
    )
    _assert_matches(cas, "2024-25")


def test_xc_stcg_excluded_from_112a():
    """Held under 12 months → STCG → must NOT appear in 112A (LTCG-only schedule)."""
    cas = _cas_with(
        [
            _scheme(
                _ISIN_PRE2018_A,
                [
                    _txn(date(2024, 1, 1), CTxn.PURCHASE, "10", "100", "1000"),
                    _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-10", "120", "-1200"),
                    _stt(date(2024, 8, 1), "1.20"),
                ],
            )
        ]
    )
    # Both should report zero LTCG total for this FY
    assert _folioman_total_112a_balance(cas, "2024-25") == Decimal("0")
    assert _casparser_total_112a_balance(cas, "FY2024-25") == Decimal("0")


def test_csv_byte_matches_casparser_single_lot():
    """Whole row, headers + values, must equal casparser's CSV output verbatim."""
    cas = _cas(
        [
            _txn(date(2022, 1, 1), CTxn.PURCHASE, "100", "10", "1000"),
            _txn(date(2024, 8, 1), CTxn.REDEMPTION, "-60", "25", "-1500"),
        ]
    )
    casparser_csv = casparser.CapitalGainsReport(cas).generate_112a_csv_data("FY2024-25")

    stmt = parser.map_cas_data(cas)
    txns = parser.transactions_from_mf_statement(stmt)
    lines = compute_gain_lines(txns, get_policy("IN"))
    rows = compute_schedule_112a(lines, "2024-25", integrity_by_security=_all_reconciled(lines))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(SCHEDULE_112A_CSV_COLUMNS))
    writer.writeheader()
    for row in rows:
        writer.writerow(row.to_csv_dict())
    folioman_csv = out.getvalue()

    assert folioman_csv.strip().splitlines() == casparser_csv.strip().splitlines()
