"""XIRR solver."""

from datetime import date
from decimal import Decimal

import pytest
from folioman_core.xirr import (
    CashFlow,
    _bisect_xirr,
    cashflows_from_transactions,
    compute_xirr,
)


def test_xirr_two_flows_approximately_ten_percent():
    # Exactly 365 days apart so the annualized rate matches 10% with a 365-day year basis.
    flows = [
        CashFlow(date=date(2023, 1, 1), amount=Decimal("-10000")),
        CashFlow(date=date(2024, 1, 1), amount=Decimal("11000")),
    ]
    rate = compute_xirr(flows)
    assert rate is not None
    assert rate == pytest.approx(0.1, rel=1e-4)


def test_xirr_multi_flow_mf_portfolio():
    flows = [
        CashFlow(date=date(2024, 1, 15), amount=Decimal("-5000")),
        CashFlow(date=date(2024, 4, 10), amount=Decimal("-3000")),
        CashFlow(date=date(2024, 8, 20), amount=Decimal("1500")),
        CashFlow(date=date(2025, 1, 15), amount=Decimal("7200")),
    ]
    rate = compute_xirr(flows)
    assert rate is not None
    # Solver self-check: NPV at the returned rate should be ~0.
    assert rate == pytest.approx(0.1045, rel=1e-3)


def test_xirr_returns_none_for_single_flow():
    assert compute_xirr([CashFlow(date=date(2024, 1, 1), amount=Decimal("-1000"))]) is None


def test_xirr_returns_none_for_all_zero_flows():
    flows = [
        CashFlow(date=date(2024, 1, 1), amount=Decimal("0")),
        CashFlow(date=date(2025, 1, 1), amount=Decimal("0")),
    ]
    assert compute_xirr(flows) is None


def test_bisect_xirr_finds_root():
    # -1000 now, +1100 in a year → 10%
    rate = _bisect_xirr([(0.0, -1000.0), (1.0, 1100.0)], tolerance=1e-7)
    assert rate == pytest.approx(0.1, rel=1e-4)


def test_bisect_xirr_returns_none_without_sign_change():
    assert _bisect_xirr([(0.0, 1000.0), (1.0, 1100.0)], tolerance=1e-7) is None


def test_xirr_returns_none_without_sign_change():
    # all inflows, no investment outflow → no locatable root
    flows = [
        CashFlow(date=date(2024, 1, 1), amount=Decimal("1000")),
        CashFlow(date=date(2025, 1, 1), amount=Decimal("2000")),
    ]
    assert compute_xirr(flows) is None


def test_cashflows_from_transactions_groups_by_date():
    flows = cashflows_from_transactions(
        [
            (date(2024, 1, 1), Decimal("1000")),
            (date(2024, 1, 1), Decimal("500")),
            (date(2024, 6, 1), Decimal("-200")),
        ],
        present_date=date(2025, 1, 1),
        present_value=Decimal("2000"),
    )
    assert flows[0].amount == Decimal("-1500")
    assert flows[1].amount == Decimal("200")
    assert flows[-1].amount == Decimal("2000")
