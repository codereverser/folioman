"""Investors API: CRUD, family filter/move, PAN never leaks, 404s."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

pytestmark = pytest.mark.django_db


def _post(client, url, payload):
    return client.post(url, data=payload, content_type="application/json")


def _patch(client, url, payload):
    return client.patch(url, data=payload, content_type="application/json")


def test_openapi_lists_investor_and_family_routes(client):
    body = client.get("/api/openapi.json").json()
    paths = body["paths"]
    assert "/api/investors/" in paths
    assert "/api/families/" in paths
    assert "/api/families/{family_id}/aggregate" in paths


def test_create_and_retrieve_investor(client):
    resp = _post(client, "/api/investors/", {"name": "Mr Sharma", "email": "s@example.com"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Mr Sharma"
    assert body["has_pan"] is False
    iid = body["id"]
    assert client.get(f"/api/investors/{iid}").json()["email"] == "s@example.com"


def test_pan_is_accepted_but_never_returned(client):
    resp = _post(client, "/api/investors/", {"name": "PAN Person", "pan": "ABCDE1234F"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_pan"] is True
    assert "pan" not in body and "pan_hash" not in body and "pan_encrypted" not in body


def test_get_missing_investor_404(client):
    assert client.get("/api/investors/999999").status_code == 404


def test_list_filter_by_family_and_unaffiliated(client, make_family, make_investor):
    fam = make_family()
    make_investor(family=fam, name="In Family")
    make_investor(name="Solo")  # no family

    all_ids = {i["id"] for i in client.get("/api/investors/").json()}
    assert len(all_ids) == 2

    in_family = client.get("/api/investors/", {"family_id": fam.id}).json()
    assert [i["name"] for i in in_family] == ["In Family"]

    solo = client.get("/api/investors/", {"unaffiliated": "true"}).json()
    assert [i["name"] for i in solo] == ["Solo"]


def test_patch_moves_investor_into_and_out_of_family(client, make_family, make_investor):
    fam = make_family()
    inv = make_investor(name="Mover")
    # move in
    resp = _patch(client, f"/api/investors/{inv.id}", {"family_id": fam.id})
    assert resp.status_code == 200
    assert resp.json()["family_id"] == fam.id
    # rename only — family unchanged
    resp = _patch(client, f"/api/investors/{inv.id}", {"name": "Renamed"})
    assert resp.json()["name"] == "Renamed"
    assert resp.json()["family_id"] == fam.id
    # explicit null clears the family (move to solo)
    resp = _patch(client, f"/api/investors/{inv.id}", {"family_id": None})
    assert resp.json()["family_id"] is None


def test_pan_backfill_allowed_before_any_import(client, make_investor):
    inv = make_investor()
    assert client.get(f"/api/investors/{inv.id}").json()["pan_locked"] is False
    resp = _patch(client, f"/api/investors/{inv.id}", {"pan": "ABCDE1234F"})
    assert resp.status_code == 200
    assert resp.json()["has_pan"] is True


def test_pan_locks_once_data_imported_and_change_is_refused(
    client, make_investor, make_security, make_folio, make_transaction
):
    # PAN is the join key statements attach to: once data is imported under this
    # investor, changing it would strand that data, so it's locked.
    inv = make_investor()
    _patch(client, f"/api/investors/{inv.id}", {"pan": "ABCDE1234F"})
    folio = make_folio(investor=inv)
    make_transaction(
        investor=inv,
        security=make_security(),
        folio=folio,
        date=dt.date(2025, 1, 1),
        units=Decimal("10"),
        nav_or_price=Decimal("10"),
    )

    assert client.get(f"/api/investors/{inv.id}").json()["pan_locked"] is True

    # A different PAN is rejected; resending the same PAN is a harmless no-op.
    assert _patch(client, f"/api/investors/{inv.id}", {"pan": "ZYXWV9876Z"}).status_code == 409
    assert _patch(client, f"/api/investors/{inv.id}", {"pan": "ABCDE1234F"}).status_code == 200
    # Non-PAN edits still work while locked.
    assert _patch(client, f"/api/investors/{inv.id}", {"name": "Renamed"}).status_code == 200


def test_get_returns_masked_pan_for_disambiguation(client, make_investor):
    inv = make_investor()
    _patch(client, f"/api/investors/{inv.id}", {"pan": "ABCDE1234F"})
    body = client.get(f"/api/investors/{inv.id}").json()
    assert body["pan_masked"] == "XXXXXX234F"  # last 4 kept, rest masked
    assert "pan" not in body and "pan_hash" not in body  # full value never exposed


def test_duplicate_pan_is_rejected_cleanly(client, make_investor):
    a = make_investor()
    b = make_investor()
    _patch(client, f"/api/investors/{a.id}", {"pan": "ABCDE1234F"})
    resp = _patch(client, f"/api/investors/{b.id}", {"pan": "ABCDE1234F"})
    assert resp.status_code == 409


def test_patch_can_set_pan(client, make_investor):
    inv = make_investor(name="No PAN yet")
    resp = _patch(client, f"/api/investors/{inv.id}", {"pan": "ABCDE1234F"})
    assert resp.status_code == 200
    assert resp.json()["has_pan"] is True


def test_delete_investor(client, make_investor):
    inv = make_investor()
    assert client.delete(f"/api/investors/{inv.id}").status_code == 204
    assert client.get(f"/api/investors/{inv.id}").status_code == 404


def test_list_folios_for_investor(client, make_folio):
    folio = make_folio(number="12345/67")
    resp = client.get(f"/api/investors/{folio.investor_id}/folios")
    assert resp.status_code == 200
    assert resp.json()[0]["number"] == "12345/67"


def test_list_securities_returns_touched_securities(
    client, make_investor, make_security, make_transaction
):
    """The acquirer-picker endpoint lists securities the investor has transacted/held."""
    from folioman_core.models import SecurityType

    inv = make_investor(name="Holder")
    hdfcbank = make_security(
        security_type=SecurityType.EQUITY.value,
        name="HDFC Bank Ltd",
        isin="INE040A01034",
        symbol="HDFCBANK",
    )
    make_transaction(investor=inv, security=hdfcbank)

    rows = client.get(f"/api/investors/{inv.id}/securities").json()
    assert "INE040A01034" in {r["isin"] for r in rows}
    assert any(r["symbol"] == "HDFCBANK" for r in rows)
