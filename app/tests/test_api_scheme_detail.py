"""Scheme-detail endpoint: per-(investor, security) identity, metrics, NAV
history, and ledger in a single call."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import NAVHistory
from folioman_core.models import SecurityType

pytestmark = pytest.mark.django_db


def test_scheme_detail_reports_metrics_history_and_ledger(
    client, make_investor, make_security, make_folio, make_transaction
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value, name="Acme Flexi Cap")
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

    body = client.get(f"/api/investors/{inv.id}/holdings/{mf.id}", {"as_of": "2025-06-01"}).json()

    assert body["security"]["name"] == "Acme Flexi Cap"
    assert Decimal(str(body["units"])) == Decimal("100")
    assert Decimal(str(body["value_inr"])) == Decimal("2000")  # 100 * 20
    assert Decimal(str(body["invested_inr"])) == Decimal("1000")  # FIFO cost basis
    assert body["return_pct"] == pytest.approx(1.0)  # (2000-1000)/1000
    assert Decimal(str(body["latest_nav"])) == Decimal("20")
    assert body["latest_nav_date"] == "2025-06-01"
    # day change = 100 * (20 - 18) = 200
    assert Decimal(str(body["day_change_inr"])) == Decimal("200")
    assert body["has_transactions"] is True
    assert len(body["nav_history"]) == 2
    assert len(body["transactions"]) == 1
    assert body["xirr"] is not None
    assert body["xirr_status"] == "valid"  # bought >= 1 year before as_of


def test_scheme_detail_snapshot_only_has_no_transactions(
    client, make_investor, make_security, make_holding
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    make_holding(investor=inv, security=mf, units=Decimal("50"), as_of_date=dt.date(2025, 6, 1))
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("12"))

    body = client.get(f"/api/investors/{inv.id}/holdings/{mf.id}", {"as_of": "2025-06-01"}).json()

    assert body["has_transactions"] is False
    assert body["transactions"] == []
    assert Decimal(str(body["value_inr"])) == Decimal("600")  # 50 * 12
    assert body["xirr"] is None  # no cashflows
    assert body["xirr_status"] == "estimated"  # snapshot-only


def test_scheme_detail_flags_short_holding_xirr(
    client, make_investor, make_security, make_folio, make_transaction
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    folio = make_folio(investor=inv)
    # Bought ~3 months before as_of → XIRR is annualized over < 1 year.
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2025, 3, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("12"))

    body = client.get(f"/api/investors/{inv.id}/holdings/{mf.id}", {"as_of": "2025-06-01"}).json()

    assert body["xirr_status"] == "less_than_1_year"


def test_scheme_detail_reports_per_folio_balances(
    client, make_investor, make_security, make_folio, make_transaction
):
    # The same fund held across two folios: the breakdown reports each folio's
    # net units and value at the latest NAV, largest first.
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value, name="Acme Flexi Cap")
    f1 = make_folio(investor=inv, number="F-AAA")
    f2 = make_folio(investor=inv, number="F-BBB")
    make_transaction(
        investor=inv,
        security=mf,
        folio=f1,
        date=dt.date(2024, 6, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    make_transaction(
        investor=inv,
        security=mf,
        folio=f2,
        date=dt.date(2024, 6, 1),
        units=Decimal("30"),
        nav_or_price=Decimal("10"),
    )
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("20"))

    body = client.get(f"/api/investors/{inv.id}/holdings/{mf.id}", {"as_of": "2025-06-01"}).json()
    folios = body["folios"]
    assert [f["number"] for f in folios] == ["F-AAA", "F-BBB"]  # largest first
    assert Decimal(str(folios[0]["units"])) == Decimal("100")
    assert Decimal(str(folios[0]["value_inr"])) == Decimal("2000")  # 100 * 20
    assert Decimal(str(folios[1]["units"])) == Decimal("30")


def test_scheme_detail_excludes_fully_exited_folio(
    client, make_investor, make_security, make_folio, make_transaction
):
    # A folio bought and fully sold has a zero balance — it isn't a current holding,
    # so it's left out of the breakdown.
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    folio = make_folio(investor=inv, number="F-GONE")
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2024, 1, 1),
        units=Decimal("50"),
        nav_or_price=Decimal("10"),
    )
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        transaction_type="sell",
        date=dt.date(2024, 9, 1),
        units=Decimal("50"),
        nav_or_price=Decimal("15"),
    )
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("20"))

    body = client.get(f"/api/investors/{inv.id}/holdings/{mf.id}", {"as_of": "2025-06-01"}).json()
    assert body["folios"] == []


def test_scheme_detail_unheld_security_404s(client, make_investor, make_security):
    inv = make_investor()
    other = make_security(security_type=SecurityType.MF.value)

    assert client.get(f"/api/investors/{inv.id}/holdings/{other.id}").status_code == 404


def test_scheme_detail_unknown_investor_404s(client, make_security):
    mf = make_security()
    assert client.get(f"/api/investors/999999/holdings/{mf.id}").status_code == 404
