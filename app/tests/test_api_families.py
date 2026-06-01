"""Families API: CRUD, demote-on-delete, combined aggregate valuation."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import Investor, NAVHistory
from folioman_core.models import SecurityType

pytestmark = pytest.mark.django_db


def _post(client, url, payload):
    return client.post(url, data=payload, content_type="application/json")


def test_create_family_starts_empty(client):
    resp = _post(client, "/api/families/", {"name": "Sharma Family"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Sharma Family"
    assert body["investor_count"] == 0


def test_family_detail_embeds_investors(client, make_family, make_investor):
    fam = make_family()
    make_investor(family=fam, name="Mr S")
    make_investor(family=fam, name="Mrs S")
    body = client.get(f"/api/families/{fam.id}").json()
    assert body["investor_count"] == 2
    assert {i["name"] for i in body["investors"]} == {"Mr S", "Mrs S"}


def test_delete_family_demotes_investors_to_solo(client, make_family, make_investor):
    fam = make_family()
    ids = [make_investor(family=fam).id for _ in range(3)]
    assert client.delete(f"/api/families/{fam.id}").status_code == 204
    # investors survive, now unaffiliated
    for iid in ids:
        assert Investor.objects.get(id=iid).family_id is None


def test_get_missing_family_404(client):
    assert client.get("/api/families/999999").status_code == 404


def test_family_aggregate_sums_in_inr(
    client, make_family, make_investor, make_security, make_holding
):
    fam = make_family()
    inv1 = make_investor(family=fam)
    inv2 = make_investor(family=fam)
    mf = make_security(security_type=SecurityType.MF.value)
    eq = make_security(
        security_type=SecurityType.EQUITY.value, isin="INE002A01018", symbol="RELIANCE"
    )
    make_holding(investor=inv1, security=mf, units=Decimal("100"), as_of_date=dt.date(2025, 6, 1))
    make_holding(investor=inv2, security=eq, units=Decimal("10"), as_of_date=dt.date(2025, 6, 1))
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("75"))
    NAVHistory.objects.create(security=eq, date=dt.date(2025, 6, 1), nav=Decimal("2850"))

    body = client.get(f"/api/families/{fam.id}/aggregate", {"as_of": "2025-06-01"}).json()
    # 100*75 + 10*2850 = 36,000
    assert Decimal(str(body["total_inr"])) == Decimal("36000")
    assert body["investor_count"] == 2
    assert body["stale_count"] == 0
    mix = {row["security_type"]: Decimal(str(row["value_inr"])) for row in body["asset_mix"]}
    assert mix["mf"] == Decimal("7500")
    assert mix["equity"] == Decimal("28500")
    # top holdings sorted by value desc
    assert body["top_holdings"][0]["security_type"] == "equity"


def test_family_aggregate_without_prices_is_stale(
    client, make_family, make_investor, make_security, make_holding
):
    fam = make_family()
    inv = make_investor(family=fam)
    mf = make_security(security_type=SecurityType.MF.value)
    make_holding(investor=inv, security=mf, units=Decimal("100"), as_of_date=dt.date(2025, 6, 1))
    # no NAVHistory -> no price -> stale, total 0
    body = client.get(f"/api/families/{fam.id}/aggregate", {"as_of": "2025-06-01"}).json()
    assert Decimal(str(body["total_inr"])) == Decimal("0")
    assert body["stale_count"] == 1
