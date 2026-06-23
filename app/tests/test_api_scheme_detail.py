"""Scheme-detail endpoint: per-(investor, security) identity, metrics, NAV
history, and ledger in a single call."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import AppliedCorporateAction, NAVHistory
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


def test_scheme_detail_corporate_actions_show_ratio_and_running_balance(
    client, make_investor, make_security, make_folio, make_transaction, make_holding
):
    # An acquirer reached entirely through a merger then a bonus: the corporate-actions
    # timeline must show each event's ratio (and the merged-away scrip) with the balance
    # it produced — and the holding reconciles, so it isn't flagged partial.
    inv = make_investor()
    folio = make_folio(investor=inv)
    old = make_security(
        security_type=SecurityType.EQUITY.value, isin="INE001A01036", symbol="OLDCO", name="OLDCO"
    )
    new = make_security(
        security_type=SecurityType.EQUITY.value, isin="INE040A01034", symbol="NEWCO", name="NEWCO"
    )
    make_transaction(
        investor=inv,
        security=old,
        folio=folio,
        date=dt.date(2022, 6, 27),
        units=Decimal("50"),
        nav_or_price=Decimal("2200"),
    )
    AppliedCorporateAction.objects.create(
        investor=inv,
        security=old,
        counterparty_security=new,
        kind="merger",
        ex_date=dt.date(2023, 7, 1),
        merger_ratio=Decimal("1.68"),  # 50 old -> 84 new
        source_ref="merger-test",
    )
    AppliedCorporateAction.objects.create(
        investor=inv,
        security=new,
        kind="bonus",
        ex_date=dt.date(2025, 8, 26),
        unit_multiplier=Decimal("2"),
        bonus_ratio_a=1,
        bonus_ratio_b=1,
        source_ref="bonus-test",
    )
    make_holding(investor=inv, security=new, units=Decimal("168"), as_of_date=dt.date(2025, 9, 1))

    body = client.get(f"/api/investors/{inv.id}/holdings/{new.id}", {"as_of": "2025-09-01"}).json()

    assert Decimal(str(body["units"])) == Decimal("168")
    assert body["partial_history"] is False
    cas = {c["kind"]: c for c in body["corporate_actions"]}
    assert cas["merger"]["ratio"] == "1.68 new per old"
    assert cas["merger"]["counterparty"] == "OLDCO"
    assert Decimal(str(cas["merger"]["units_after"])) == Decimal("84")
    assert cas["bonus"]["ratio"] == "1:1"
    assert Decimal(str(cas["bonus"]["units_added"])) == Decimal("84")
    assert Decimal(str(cas["bonus"]["units_after"])) == Decimal("168")


def test_scheme_detail_ledger_is_as_traded_with_corporate_action_delta_rows(
    client, make_investor, make_security, make_folio, make_transaction, make_holding
):
    """Trades keep their original tradebook units/prices; a split shows as a +units
    event, not by rescaling the buy. The running balance still reaches the holding."""
    inv = make_investor()
    folio = make_folio(investor=inv)
    sec = make_security(
        security_type=SecurityType.EQUITY.value, isin="INE111A01011", symbol="ACME", name="Acme"
    )
    make_transaction(
        investor=inv,
        security=sec,
        folio=folio,
        date=dt.date(2020, 1, 1),
        units=Decimal("10"),
        nav_or_price=Decimal("100"),
    )
    AppliedCorporateAction.objects.create(
        investor=inv,
        security=sec,
        kind="split",
        ex_date=dt.date(2021, 6, 1),
        unit_multiplier=Decimal("2"),
        source_ref="split-test",
    )
    make_holding(investor=inv, security=sec, units=Decimal("20"), as_of_date=dt.date(2021, 7, 1))

    body = client.get(f"/api/investors/{inv.id}/holdings/{sec.id}", {"as_of": "2021-07-01"}).json()
    txns = body["transactions"]

    buy = next(t for t in txns if t["transaction_type"] == "buy")
    assert Decimal(str(buy["units"])) == Decimal("10")  # original, not split-scaled to 20
    assert Decimal(str(buy["nav_or_price"])) == Decimal("100")  # original price
    assert Decimal(str(buy["balance"])) == Decimal("10")

    split = next(t for t in txns if t["transaction_type"] == "split")
    assert Decimal(str(split["units"])) == Decimal("10")  # +units the split added
    assert Decimal(str(split["balance"])) == Decimal("20")  # reaches the holding


def test_scheme_detail_unheld_security_404s(client, make_investor, make_security):
    inv = make_investor()
    other = make_security(security_type=SecurityType.MF.value)

    assert client.get(f"/api/investors/{inv.id}/holdings/{other.id}").status_code == 404


def test_scheme_detail_unknown_investor_404s(client, make_security):
    mf = make_security()
    assert client.get(f"/api/investors/999999/holdings/{mf.id}").status_code == 404
