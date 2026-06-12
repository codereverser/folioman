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


# --- FY-transition / grandfathering boundary edges ----------------------------


def _fmv(value: str):
    def lookup(isin: str, _on: date) -> Decimal | None:
        return Decimal(value) if isin == _EQUITY.isin else None

    return lookup


def _ltcg_disposal(*, acquired_on: date, cost: str, sale: str, units: str = "10") -> Disposal:
    return Disposal(
        security=_EQUITY,
        acquired_on=acquired_on,
        sold_on=date(2024, 8, 1),  # well after a year → LONG
        units=units,
        sale_price_per_unit=sale,
        cost_per_unit=cost,
    )


def test_acquisition_on_grandfather_cutoff_is_grandfathered(policy: IndiaTaxPolicy):
    # Acquired exactly on 2018-01-31 (the BE cutoff is inclusive) → grandfathered.
    on_cutoff = _ltcg_disposal(acquired_on=GRANDFATHER_ACQUIRE_CUTOFF, cost="5", sale="100")
    assert policy.adjusted_cost(on_cutoff, fmv_lookup=_fmv("90")) == Decimal("900.00")
    # One day later (2018-02-01, AE) → no grandfathering, original cost stands.
    day_after = _ltcg_disposal(acquired_on=date(2018, 2, 1), cost="5", sale="100")
    assert policy.adjusted_cost(day_after, fmv_lookup=_fmv("90")) == Decimal("50.00")


def test_grandfather_fmv_equal_to_cost_or_above_sale(policy: IndiaTaxPolicy):
    # FMV == cost → cost unchanged.
    at_cost = _ltcg_disposal(acquired_on=date(2017, 1, 1), cost="50", sale="100")
    assert policy.adjusted_cost(at_cost, fmv_lookup=_fmv("50")) == Decimal("500.00")
    # FMV above the sale price → capped at the sale price: max(20, min(150, 100)) = 100.
    capped = _ltcg_disposal(acquired_on=date(2017, 1, 1), cost="20", sale="100")
    assert policy.adjusted_cost(capped, fmv_lookup=_fmv("150")) == Decimal("1000.00")


def test_grandfather_does_not_inflate_cost_on_a_loss(policy: IndiaTaxPolicy):
    # Sold below cost. FMV above the sale must NOT lift cost above the actual cost:
    # max(100, min(200, 60)) = 100 → original cost kept, the real loss survives.
    loss = _ltcg_disposal(acquired_on=date(2017, 1, 1), cost="100", sale="60")
    assert policy.adjusted_cost(loss, fmv_lookup=_fmv("200")) == Decimal("1000.00")


def test_long_term_leap_day_anniversary(policy: IndiaTaxPolicy):
    # Acquired on a leap day; the 12-month anniversary clamps to Feb 28 in the
    # non-leap next year. Sold on the clamped anniversary == 12 months → SHORT;
    # one day past it → LONG (calendar reckoning, not a 365-day proxy).
    base = Disposal(
        security=_EQUITY,
        acquired_on=date(2020, 2, 29),
        sold_on=date(2021, 2, 28),
        units="1",
        sale_price_per_unit="10",
        cost_per_unit="5",
    )
    one_day_later = base.model_copy(update={"sold_on": date(2021, 3, 1)})
    assert policy.classify_term(base, asset_type=SecurityType.EQUITY) is Term.SHORT
    assert policy.classify_term(one_day_later, asset_type=SecurityType.EQUITY) is Term.LONG
