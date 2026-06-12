"""Schedule 112A India report."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from folioman_core.models import (
    Security,
    SecurityType,
    Transaction,
    TransactionSource,
    TransactionType,
)
from folioman_core.reconciliation import IntegrityStatus
from folioman_core.tax import compute_gain_lines, compute_schedule_112a, get_policy
from folioman_core.tax.schedule_112a import SCHEDULE_112A_CSV_COLUMNS

_EQUITY = Security(
    type=SecurityType.EQUITY,
    name="Reliance Industries Ltd",
    isin="INE002A01018",
    symbol="RELIANCE",
)


def _golden_book() -> list[Transaction]:
    return [
        Transaction(
            security=_EQUITY,
            date=date(2017, 1, 1),
            type=TransactionType.BUY,
            units="10",
            nav_or_price="50",
            amount="500",
            source=TransactionSource.MANUAL,
        ),
        Transaction(
            security=_EQUITY,
            date=date(2024, 8, 1),
            type=TransactionType.SELL,
            units="10",
            nav_or_price="150",
            source=TransactionSource.MANUAL,
        ),
    ]


def test_schedule_112a_csv_columns_match_official_template():
    assert len(SCHEDULE_112A_CSV_COLUMNS) == 15
    assert SCHEDULE_112A_CSV_COLUMNS[0] == "Share/Unit acquired(1a)"
    assert SCHEDULE_112A_CSV_COLUMNS[1] == "Share/Unit Transferred(1b)"


def test_schedule_112a_golden_fixture(fixtures_dir: Path):
    expected = json.loads((fixtures_dir / "schedule_112a_expected.json").read_text())
    fmv = {"INE002A01018": Decimal("90")}

    def fmv_lookup(isin: str, on: date) -> Decimal | None:
        return fmv.get(isin)

    lines = compute_gain_lines(_golden_book(), get_policy("IN"), fmv_lookup=fmv_lookup)
    rows = compute_schedule_112a(
        lines,
        expected["fy_label"],
        integrity_by_security={_EQUITY: IntegrityStatus.RECONCILED},
        fmv_lookup=fmv_lookup,
    )
    assert len(rows) == len(expected["rows"])

    row = rows[0]
    exp = expected["rows"][0]
    assert row.share_unit_acquired == exp["share_unit_acquired"]
    assert row.share_unit_transferred == exp["share_unit_transferred"]
    assert row.isin == exp["isin"]
    assert row.balance == Decimal(exp["balance"])

    csv = row.to_csv_dict()
    assert set(csv.keys()) == set(SCHEDULE_112A_CSV_COLUMNS)
    assert csv["Share/Unit acquired(1a)"] == "BE"
    assert csv["Balance(14) = 6 - 13"] == exp["balance"]


def test_schedule_112a_excludes_stcg():
    txns = [
        Transaction(
            security=_EQUITY,
            date=date(2024, 1, 1),
            type=TransactionType.BUY,
            units="10",
            nav_or_price="100",
            amount="1000",
            source=TransactionSource.MANUAL,
        ),
        Transaction(
            security=_EQUITY,
            date=date(2024, 6, 1),
            type=TransactionType.SELL,
            units="10",
            nav_or_price="110",
            source=TransactionSource.MANUAL,
        ),
    ]
    lines = compute_gain_lines(txns, get_policy("IN"))
    rows = compute_schedule_112a(lines, "2024-25")
    assert rows == []


def test_schedule_112a_excludes_mismatch_without_flag():
    lines = compute_gain_lines(_golden_book(), get_policy("IN"))
    rows = compute_schedule_112a(
        lines,
        "2024-25",
        integrity_by_security={_EQUITY: IntegrityStatus.MISMATCH},
    )
    assert rows == []


def test_schedule_112a_includes_mismatch_when_forced():
    lines = compute_gain_lines(_golden_book(), get_policy("IN"))
    rows = compute_schedule_112a(
        lines,
        "2024-25",
        include_unreconciled=True,
        integrity_by_security={_EQUITY: IntegrityStatus.MISMATCH},
    )
    assert len(rows) == 1


def test_schedule_112a_excludes_security_with_no_integrity_status():
    """Fail closed: a disposed security absent from the integrity map (never
    reconciled, or reconcile failed) is excluded — even when forced."""
    lines = compute_gain_lines(_golden_book(), get_policy("IN"))
    assert compute_schedule_112a(lines, "2024-25", integrity_by_security={}) == []
    assert (
        compute_schedule_112a(lines, "2024-25", include_unreconciled=True, integrity_by_security={})
        == []
    )


def test_schedule_112a_ae_row_keeps_real_isin():
    txns = [
        Transaction(
            security=_EQUITY,
            date=date(2020, 1, 1),  # acquired after 31-Jan-2018 -> AE
            type=TransactionType.BUY,
            units="10",
            nav_or_price="100",
            amount="1000",
            source=TransactionSource.MANUAL,
        ),
        Transaction(
            security=_EQUITY,
            date=date(2024, 8, 1),
            type=TransactionType.SELL,
            units="10",
            nav_or_price="200",
            source=TransactionSource.MANUAL,
        ),
    ]
    rows = compute_schedule_112a(
        compute_gain_lines(txns, get_policy("IN")),
        "2024-25",
        integrity_by_security={_EQUITY: IntegrityStatus.RECONCILED},
    )
    assert len(rows) == 1
    assert rows[0].share_unit_acquired == "AE"
    assert rows[0].isin == "INE002A01018"  # real ISIN, not the INNOTREQUIRD sentinel


def test_schedule_112a_excludes_user_acknowledged_even_when_forced():
    lines = compute_gain_lines(_golden_book(), get_policy("IN"))
    rows = compute_schedule_112a(
        lines,
        "2024-25",
        include_unreconciled=True,
        integrity_by_security={_EQUITY: IntegrityStatus.USER_ACKNOWLEDGED},
    )
    assert rows == []


def test_schedule_112a_excludes_non_equity_oriented_mf():
    debt = Security(type=SecurityType.MF, name="Debt Fund", isin="INF000000001", amfi_code="999")
    txns = [
        Transaction(
            security=debt,
            date=date(2020, 1, 1),
            type=TransactionType.BUY,
            units="100",
            nav_or_price="10",
            amount="1000",
            source=TransactionSource.MANUAL,
        ),
        Transaction(
            security=debt,
            date=date(2024, 8, 1),
            type=TransactionType.SELL,
            units="100",
            nav_or_price="15",
            source=TransactionSource.MANUAL,
        ),
    ]
    rows = compute_schedule_112a(compute_gain_lines(txns, get_policy("IN")), "2024-25")
    assert rows == []


def test_schedule_112a_integrity_lookup_survives_name_drift():
    """Regression for the security-equality bug.

    Reconciliation populates ``integrity_by_security`` from one source (e.g.
    CAS-derived Security with one scheme-name spelling); ``compute_schedule_112a``
    looks it up with the Security carried on each disposal (which may have
    been built from a different source with a normalised name). The lookup
    must hit on identity, not on the descriptive ``name``, or eligible LTCG
    rows are silently dropped from the export.
    """
    cas_security = Security(
        type=SecurityType.EQUITY,
        name="Reliance Industries Ltd.",  # trailing period from one source
        isin="INE002A01018",
        symbol="RELIANCE",
    )
    manual_security = Security(
        type=SecurityType.EQUITY,
        name="Reliance Industries",  # no trailing period from the other
        isin="INE002A01018",
        symbol="RELIANCE",
    )
    txns = [
        Transaction(
            security=manual_security,
            date=date(2020, 1, 1),
            type=TransactionType.BUY,
            units="10",
            nav_or_price="100",
            amount="1000",
            source=TransactionSource.MANUAL,
        ),
        Transaction(
            security=manual_security,
            date=date(2024, 8, 1),
            type=TransactionType.SELL,
            units="10",
            nav_or_price="200",
            source=TransactionSource.MANUAL,
        ),
    ]
    lines = compute_gain_lines(txns, get_policy("IN"))
    # Integrity map is keyed by the CAS-side Security (different name).
    # With identity-based equality, the lookup must still hit and admit
    # the row into the 112A export.
    rows = compute_schedule_112a(
        lines,
        "2024-25",
        integrity_by_security={cas_security: IntegrityStatus.RECONCILED},
    )
    assert len(rows) == 1, "name drift between integrity map and disposals must not drop rows"


# --- FY-transition boundary edges ---------------------------------------------


def _buy(units: str, nav: str, on: date) -> Transaction:
    return Transaction(
        security=_EQUITY,
        date=on,
        type=TransactionType.BUY,
        units=units,
        nav_or_price=nav,
        amount=str(Decimal(units) * Decimal(nav)),
        source=TransactionSource.MANUAL,
    )


def _sell(units: str, nav: str, on: date) -> Transaction:
    return Transaction(
        security=_EQUITY,
        date=on,
        type=TransactionType.SELL,
        units=units,
        nav_or_price=nav,
        source=TransactionSource.MANUAL,
    )


_RECONCILED = {_EQUITY: IntegrityStatus.RECONCILED}


def test_schedule_112a_fy_filter_inclusive_on_mar31_excludes_apr1():
    # Two LTCG sells straddling the FY 2024-25 / 2025-26 boundary, distinct prices.
    book = [
        _buy("20", "50", date(2017, 1, 1)),
        _sell("10", "150", date(2025, 3, 31)),  # last day of FY 2024-25
        _sell("10", "200", date(2025, 4, 1)),  # first day of FY 2025-26
    ]
    lines = compute_gain_lines(book, get_policy("IN"))
    rows_2425 = compute_schedule_112a(lines, "2024-25", integrity_by_security=_RECONCILED)
    rows_2526 = compute_schedule_112a(lines, "2025-26", integrity_by_security=_RECONCILED)

    assert len(rows_2425) == 1 and len(rows_2526) == 1  # one sell each FY
    # The Mar-31 sell (1500) is in 2024-25; the Apr-1 sell (2000) in 2025-26.
    assert rows_2425[0].full_value_consideration == Decimal("1500.00")
    assert rows_2526[0].full_value_consideration == Decimal("2000.00")


def test_schedule_112a_bucketed_by_sale_fy_not_acquisition_fy():
    # Bought in FY 2016-17, sold in FY 2024-25 → reported only in the sale's FY.
    book = [_buy("10", "50", date(2017, 1, 1)), _sell("10", "150", date(2024, 8, 1))]
    lines = compute_gain_lines(book, get_policy("IN"))
    assert compute_schedule_112a(lines, "2016-17", integrity_by_security=_RECONCILED) == []
    assert len(compute_schedule_112a(lines, "2024-25", integrity_by_security=_RECONCILED)) == 1


def test_schedule_112a_transfer_bucket_col1b_on_2024_07_23_cutoff():
    # Col 1b (transfer regime) splits on 2024-07-23: the cutoff itself is AE, the
    # day before is BE. Both sells fall in FY 2024-25.
    on_cutoff = compute_schedule_112a(
        compute_gain_lines(
            [_buy("10", "50", date(2017, 1, 1)), _sell("10", "150", date(2024, 7, 23))],
            get_policy("IN"),
        ),
        "2024-25",
        integrity_by_security=_RECONCILED,
    )
    day_before = compute_schedule_112a(
        compute_gain_lines(
            [_buy("10", "50", date(2017, 1, 1)), _sell("10", "150", date(2024, 7, 22))],
            get_policy("IN"),
        ),
        "2024-25",
        integrity_by_security=_RECONCILED,
    )
    assert on_cutoff[0].share_unit_transferred == "AE"
    assert day_before[0].share_unit_transferred == "BE"
