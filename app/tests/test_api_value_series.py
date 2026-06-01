"""Value-series (net worth over time) + XIRR wiring.

The series is reconstructed on demand from the ledger + NAVHistory — no snapshot
tables. Covers: units changing across a sell date, the final point matching the
point-in-time summary, stale flagging for an unpriced holding, and XIRR.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import NAVHistory
from folioman_core.models import SecurityType, TransactionType

pytestmark = pytest.mark.django_db


def _points_by_date(body) -> dict[str, dict]:
    return {p["date"]: p for p in body["points"]}


def test_value_series_reconstructs_units_across_a_sell(
    client, make_investor, make_security, make_folio, make_transaction
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    folio = make_folio(investor=inv)
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2025, 1, 15),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2025, 4, 15),
        transaction_type=TransactionType.SELL.value,
        units=Decimal("40"),
        nav_or_price=Decimal("20"),
    )
    for d, nav in [((1, 31), 12), ((3, 31), 15), ((6, 30), 20)]:
        NAVHistory.objects.create(security=mf, date=dt.date(2025, *d), nav=Decimal(str(nav)))

    body = client.get(
        f"/api/investors/{inv.id}/value-series",
        {"from": "2025-01-01", "to": "2025-06-30", "granularity": "monthly"},
    ).json()
    pts = _points_by_date(body)

    # Before the first buy: nothing held.
    assert Decimal(str(pts["2025-01-01"]["value_inr"])) == Decimal("0")
    assert Decimal(str(pts["2025-01-01"]["invested_inr"])) == Decimal("0")
    # Feb: 100 units priced at the latest NAV on/before (Jan 31 → 12).
    assert Decimal(str(pts["2025-02-01"]["value_inr"])) == Decimal("1200")
    assert Decimal(str(pts["2025-02-01"]["invested_inr"])) == Decimal("1000")
    # May, after the Apr 15 sell: 60 units; NAV on/before is Mar 31 → 15.
    assert Decimal(str(pts["2025-05-01"]["value_inr"])) == Decimal("900")
    # invested = FIFO cost basis of the 60 units still held = 60 * 10 (not net cash).
    assert Decimal(str(pts["2025-05-01"]["invested_inr"])) == Decimal("600")
    # Final point lands exactly on `to` (Jun 30): 60 * 20.
    assert Decimal(str(pts["2025-06-30"]["value_inr"])) == Decimal("1200")


def test_invested_is_fifo_cost_basis_never_net_cash(
    client, make_investor, make_security, make_folio, make_transaction
):
    # A profitable partial sell must NOT push the "Invested" line negative while
    # units are still held: invested tracks cost basis of remaining units, not
    # cumulative net cash (which would be 1000 - 60*30 = -800 here).
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    folio = make_folio(investor=inv)
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2025, 1, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2025, 2, 1),
        transaction_type=TransactionType.SELL.value,
        units=Decimal("60"),
        nav_or_price=Decimal("30"),
    )

    body = client.get(
        f"/api/investors/{inv.id}/value-series", {"from": "2025-03-01", "to": "2025-03-01"}
    ).json()

    # 40 units still held at ₹10 cost basis → ₹400, not -₹800.
    assert Decimal(str(body["points"][-1]["invested_inr"])) == Decimal("400")


def test_value_series_final_point_equals_summary(
    client, make_investor, make_security, make_folio, make_transaction
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    folio = make_folio(investor=inv)
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2025, 1, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("75"))

    series = client.get(f"/api/investors/{inv.id}/value-series", {"to": "2025-06-01"}).json()
    summary = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()

    assert series["points"][-1]["date"] == "2025-06-01"
    assert Decimal(str(series["points"][-1]["value_inr"])) == Decimal(str(summary["total_inr"]))


def test_value_series_flags_unpriced_holding_as_stale(
    client, make_investor, make_security, make_holding
):
    inv = make_investor()
    equity = make_security(security_type=SecurityType.EQUITY.value)  # no NAVHistory
    make_holding(investor=inv, security=equity, units=Decimal("50"), as_of_date=dt.date(2025, 1, 1))

    body = client.get(
        f"/api/investors/{inv.id}/value-series", {"from": "2025-02-01", "to": "2025-04-01"}
    ).json()

    # Held but unpriced: points are flagged stale, not dropped, and contribute ₹0.
    held = [p for p in body["points"] if p["date"] >= "2025-02-01"]
    assert held and all(p["stale"] for p in held)
    assert all(Decimal(str(p["value_inr"])) == Decimal("0") for p in held)


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


def test_family_value_series_aggregates_investors(
    client, make_family, make_investor, make_security, make_folio, make_transaction
):
    fam = make_family()
    a = make_investor(family=fam)
    b = make_investor(family=fam)
    mf = make_security(security_type=SecurityType.MF.value)
    for inv in (a, b):
        make_transaction(
            investor=inv,
            security=mf,
            folio=make_folio(investor=inv),
            date=dt.date(2025, 1, 1),
            units=Decimal("100"),
            nav_or_price=Decimal("10"),
        )
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("50"))

    body = client.get(f"/api/families/{fam.id}/value-series", {"to": "2025-06-01"}).json()

    assert body["family_id"] == fam.id
    # 200 units across the two investors * 50.
    assert Decimal(str(body["points"][-1]["value_inr"])) == Decimal("10000")
