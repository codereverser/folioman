"""Value-series endpoint (reads the persisted day-wise ``InvestorValue``) + the
summary XIRR / day-change / per-fund wiring.

The series endpoint reads what the scheduler computed (``InvestorValue``); compute
correctness is covered in ``test_valuation_jobs.py``. Here: read-in-range, the
weekly/monthly downsample, family = sum of members, and XIRR on the summary.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import InvestorValue, NAVHistory
from folioman_core.models import SecurityType

pytestmark = pytest.mark.django_db


def _points_by_date(body) -> dict[str, dict]:
    return {p["date"]: p for p in body["points"]}


def _seed_values(investor, rows: list[tuple[dt.date, str, str]]) -> None:
    """Seed daily InvestorValue rows: [(date, value, invested), …]."""
    InvestorValue.objects.bulk_create(
        [
            InvestorValue(investor=investor, date=d, value_inr=Decimal(v), invested_inr=Decimal(i))
            for d, v, i in rows
        ]
    )


def test_value_series_returns_persisted_rows_in_range(client, make_investor):
    inv = make_investor()
    _seed_values(
        inv,
        [
            (dt.date(2025, 1, 1), "1000", "1000"),
            (dt.date(2025, 1, 2), "1100", "1000"),
            (dt.date(2025, 1, 3), "1200", "1000"),
        ],
    )
    body = client.get(
        f"/api/investors/{inv.id}/value-series",
        {"from": "2025-01-01", "to": "2025-01-02", "granularity": "daily"},
    ).json()
    pts = _points_by_date(body)
    assert set(pts) == {"2025-01-01", "2025-01-02"}  # 01-03 is out of range
    assert Decimal(str(pts["2025-01-02"]["value_inr"])) == Decimal("1100")


def test_value_series_downsamples_to_one_point_per_month(client, make_investor):
    inv = make_investor()
    _seed_values(
        inv,
        [
            (dt.date(2025, 1, 10), "100", "100"),
            (dt.date(2025, 1, 31), "150", "100"),  # last in Jan → kept
            (dt.date(2025, 2, 15), "200", "100"),  # last in Feb → kept
        ],
    )
    body = client.get(
        f"/api/investors/{inv.id}/value-series",
        {"from": "2025-01-01", "to": "2025-02-28", "granularity": "monthly"},
    ).json()
    pts = _points_by_date(body)
    assert set(pts) == {"2025-01-31", "2025-02-15"}  # last of each month
    assert Decimal(str(pts["2025-01-31"]["value_inr"])) == Decimal("150")


def test_value_series_empty_when_not_yet_computed(client, make_investor):
    # No InvestorValue rows (scheduler hasn't run) → empty series, no error.
    inv = make_investor()
    body = client.get(f"/api/investors/{inv.id}/value-series", {"to": "2025-06-01"}).json()
    assert body["points"] == []


def test_xirr_appears_on_summary_for_a_ledger(
    client, make_investor, make_security, make_folio, make_transaction
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    folio = make_folio(investor=inv)
    # ₹1000 in a year ago; worth ₹2000 today → ~100% annualized.
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2024, 6, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("20"))

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()

    assert body["xirr"] is not None
    assert 0.9 < body["xirr"] < 1.1


def test_xirr_is_null_without_a_ledger(client, make_investor, make_security, make_holding):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    make_holding(investor=inv, security=mf, units=Decimal("100"), as_of_date=dt.date(2025, 6, 1))
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("75"))

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()

    # Snapshot-only: no acquisition cashflows → no solvable rate.
    assert body["xirr"] is None


def test_summary_holding_carries_cost_basis_return_and_day_change(
    client, make_investor, make_security, make_folio, make_transaction
):
    # 100 units bought at ₹10 (cost basis ₹1000). Two NAV points: ₹18 → ₹20.
    # value = 100*20 = 2000; return = (2000-1000)/1000 = 1.0; day-change = 100*(20-18) = 200.
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    folio = make_folio(investor=inv)
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2024, 6, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 5, 30), nav=Decimal("18"))
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("20"))

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()
    row = body["top_holdings"][0]

    assert Decimal(str(row["invested_inr"])) == Decimal("1000")
    assert abs(row["return_pct"] - 1.0) < 1e-9
    assert Decimal(str(row["day_change_inr"])) == Decimal("200")
    assert abs(row["day_change_pct"] - (2 / 18)) < 1e-9
    assert Decimal(str(body["day_change_inr"])) == Decimal("200")


def test_day_change_is_null_with_a_single_nav_point(
    client, make_investor, make_security, make_folio, make_transaction
):
    # Only one NAV point → no prior close → no day-change delta to report.
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    folio = make_folio(investor=inv)
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2024, 6, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("20"))

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()

    assert body["top_holdings"][0]["day_change_inr"] is None
    assert body["day_change_inr"] is None


def test_per_fund_xirr_is_isolated_per_holding(
    client, make_investor, make_security, make_folio, make_transaction
):
    # Two funds bought a year ago: WINNER doubled (~100%), LAGGARD flat (~0%).
    # Each holding row carries its own money-weighted return — not the blended one.
    inv = make_investor()
    winner = make_security(security_type=SecurityType.MF.value)
    laggard = make_security(security_type=SecurityType.MF.value)
    make_transaction(
        investor=inv,
        security=winner,
        folio=make_folio(investor=inv),
        date=dt.date(2024, 6, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    make_transaction(
        investor=inv,
        security=laggard,
        folio=make_folio(investor=inv),
        date=dt.date(2024, 6, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    NAVHistory.objects.create(security=winner, date=dt.date(2025, 6, 1), nav=Decimal("20"))
    NAVHistory.objects.create(security=laggard, date=dt.date(2025, 6, 1), nav=Decimal("10"))

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()
    by_id = {r["security_id"]: r for r in body["top_holdings"]}

    assert 0.9 < by_id[winner.id]["xirr"] < 1.1  # doubled in a year
    assert abs(by_id[laggard.id]["xirr"]) < 0.05  # flat


def test_per_fund_xirr_null_for_snapshot_only_holding(
    client, make_investor, make_security, make_holding
):
    # A snapshot-only holding (eCAS, no transaction ledger) has no cashflows, so no
    # per-fund XIRR can be solved — the field is null, not a fabricated number.
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    make_holding(investor=inv, security=mf, units=Decimal("100"), as_of_date=dt.date(2025, 6, 1))
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("75"))

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()

    assert body["top_holdings"][0]["xirr"] is None


def test_family_value_series_sums_members_by_date(client, make_family, make_investor):
    # Family series = sum of members' persisted InvestorValue rows on each date.
    fam = make_family()
    a = make_investor(family=fam)
    b = make_investor(family=fam)
    _seed_values(a, [(dt.date(2025, 6, 1), "1000", "800"), (dt.date(2025, 6, 2), "1100", "800")])
    _seed_values(b, [(dt.date(2025, 6, 1), "500", "400"), (dt.date(2025, 6, 2), "550", "400")])

    body = client.get(
        f"/api/families/{fam.id}/value-series",
        {"from": "2025-06-01", "to": "2025-06-02", "granularity": "daily"},
    ).json()
    pts = _points_by_date(body)

    assert body["family_id"] == fam.id
    assert Decimal(str(pts["2025-06-01"]["value_inr"])) == Decimal("1500")  # 1000 + 500
    assert Decimal(str(pts["2025-06-02"]["value_inr"])) == Decimal("1650")  # 1100 + 550
    assert Decimal(str(pts["2025-06-02"]["invested_inr"])) == Decimal("1200")  # 800 + 400
