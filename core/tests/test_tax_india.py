"""India tax policy."""

from datetime import date
from decimal import Decimal

import pytest
from folioman_core.models import Security, SecurityType
from folioman_core.tax import get_policy
from folioman_core.tax.india import (
    GRANDFATHER_ACQUIRE_CUTOFF,
    TRANSFER_REGIME_CUTOFF,
    IndiaTaxPolicy,
    acquire_bucket,
    india_fy_label,
    india_fy_range,
    transfer_bucket,
)
from folioman_core.tax.models import Disposal, Term

_EQUITY = Security(
    type=SecurityType.EQUITY,
    name="Reliance",
    isin="INE002A01018",
    symbol="RELIANCE",
)


@pytest.fixture
def policy() -> IndiaTaxPolicy:
    return get_policy("IN")  # type: ignore[return-value]


def test_get_policy_india(policy: IndiaTaxPolicy):
    assert policy.jurisdiction_code == "IN"


def test_india_fy_label_and_range():
    assert india_fy_label(date(2024, 8, 1)) == "2024-25"
    assert india_fy_label(date(2025, 2, 1)) == "2024-25"
    assert india_fy_label(date(2025, 4, 1)) == "2025-26"
    start, end = india_fy_range("2024-25")
    assert start == date(2024, 4, 1)
    assert end == date(2025, 3, 31)


def test_acquire_and_transfer_buckets():
    assert acquire_bucket(date(2018, 1, 31)) == "BE"
    assert acquire_bucket(date(2018, 2, 1)) == "AE"
    assert transfer_bucket(date(2024, 7, 22)) == "BE"
    assert transfer_bucket(TRANSFER_REGIME_CUTOFF) == "AE"


def test_classify_term_short_and_long(policy: IndiaTaxPolicy):
    short = Disposal(
        security=_EQUITY,
        acquired_on=date(2024, 1, 1),
        sold_on=date(2024, 6, 1),
        units="10",
        sale_price_per_unit="100",
        cost_per_unit="80",
    )
    long = Disposal(
        security=_EQUITY,
        acquired_on=date(2023, 1, 1),
        sold_on=date(2024, 6, 1),
        units="10",
        sale_price_per_unit="100",
        cost_per_unit="80",
    )
    assert policy.classify_term(short, asset_type=SecurityType.EQUITY) is Term.SHORT
    assert policy.classify_term(long, asset_type=SecurityType.EQUITY) is Term.LONG


def test_classify_term_twelve_month_boundary(policy: IndiaTaxPolicy):
    # "more than 12 months", calendar-reckoned: anniversary of 2023-06-01 is
    # 2024-06-01. Sold on the anniversary == exactly 12 months -> SHORT.
    on_anniversary = Disposal(
        security=_EQUITY,
        acquired_on=date(2023, 6, 1),
        sold_on=date(2024, 6, 1),
        units="1",
        sale_price_per_unit="10",
        cost_per_unit="5",
    )
    one_day_later = on_anniversary.model_copy(update={"sold_on": date(2024, 6, 2)})
    assert policy.classify_term(on_anniversary, asset_type=SecurityType.EQUITY) is Term.SHORT
    assert policy.classify_term(one_day_later, asset_type=SecurityType.EQUITY) is Term.LONG


def test_debt_mf_not_112a_eligible_classified_short(policy: IndiaTaxPolicy):
    debt = Security(type=SecurityType.MF, name="Debt Fund", amfi_code="999")
    disposal = Disposal(
        security=debt,
        acquired_on=date(2020, 1, 1),
        sold_on=date(2024, 1, 1),  # held 4y, but not equity-oriented
        units="10",
        sale_price_per_unit="12",
        cost_per_unit="10",
    )
    assert policy.classify_term(disposal, asset_type=SecurityType.MF) is Term.SHORT


def test_equity_oriented_mf_is_long_term(policy: IndiaTaxPolicy):
    eq_mf = Security(
        type=SecurityType.MF,
        name="Equity Fund",
        amfi_code="123",
        metadata={"equity_oriented": True},
    )
    disposal = Disposal(
        security=eq_mf,
        acquired_on=date(2022, 1, 1),
        sold_on=date(2024, 1, 1),
        units="10",
        sale_price_per_unit="12",
        cost_per_unit="10",
    )
    assert policy.classify_term(disposal, asset_type=SecurityType.MF) is Term.LONG


def test_grandfathered_adjusted_cost(policy: IndiaTaxPolicy):
    disposal = Disposal(
        security=_EQUITY,
        acquired_on=date(2017, 6, 1),
        sold_on=date(2024, 6, 1),
        units="10",
        sale_price_per_unit="100",
        cost_per_unit="50",
    )

    def fmv_lookup(isin: str, on: date) -> Decimal | None:
        assert isin == "INE002A01018"
        assert on == GRANDFATHER_ACQUIRE_CUTOFF
        return Decimal("90")

    adjusted = policy.adjusted_cost(disposal, fmv_lookup=fmv_lookup)
    # max(50, min(90, 100)) * 10 = 900
    assert adjusted == Decimal("900")


def test_grandfathered_uses_sale_when_below_fmv(policy: IndiaTaxPolicy):
    disposal = Disposal(
        security=_EQUITY,
        acquired_on=date(2017, 6, 1),
        sold_on=date(2024, 6, 1),
        units="10",
        sale_price_per_unit="70",
        cost_per_unit="50",
    )

    def fmv_lookup(_isin: str, _on: date) -> Decimal | None:
        return Decimal("90")

    adjusted = policy.adjusted_cost(disposal, fmv_lookup=fmv_lookup)
    # max(50, min(90, 70)) * 10 = 700
    assert adjusted == Decimal("700")
