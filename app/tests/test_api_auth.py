"""Auth + per-advisor ownership.

Local (desktop) mode: no login, single local user, every route open. Server
mode: django-ninja-jwt bearer tokens required (401 without), login + refresh
routes, and ownership scoping so one advisor can't see another's investors
(cross-advisor id 404s — no existence leak). The JWT tests skip when the
server-only ninja_jwt dependency isn't installed.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def _post(client, url, payload, **extra):
    return client.post(url, data=payload, content_type="application/json", **extra)


# --- Local / desktop mode (the suite default) -------------------------------


def test_local_mode_needs_no_auth(client, make_investor):
    inv = make_investor()
    assert client.get(f"/api/investors/{inv.id}").status_code == 200


def test_local_mode_resolves_single_user_for_every_request(client):
    """Two requests with no auth see the same local-user-owned roster."""
    created = _post(client, "/api/investors/", {"name": "Solo"})
    assert created.status_code == 201
    roster = client.get("/api/investors/").json()
    assert [i["name"] for i in roster] == ["Solo"]


def test_token_routes_404_in_local_mode(client):
    """There is no login in local mode — the token endpoints are inert."""
    r = _post(client, "/api/auth/token/pair", {"username": "x", "password": "y"})
    assert r.status_code == 404


# --- Server / JWT mode ------------------------------------------------------


@pytest.fixture
def jwt_mode(_local_auth_mode, settings):
    """Flip to jwt auth for this test; skip if the server-only dep is absent."""
    pytest.importorskip("ninja_jwt")
    settings.FOLIOMAN_API_AUTH = "jwt"


@pytest.fixture
def advisor(django_user_model):
    return django_user_model.objects.create_user(username="adv1", password="pw-one")


def _login(client, username, password):
    return _post(client, "/api/auth/token/pair", {"username": username, "password": password})


def _bearer(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def test_jwt_required_without_token(client, jwt_mode, make_investor):
    inv = make_investor()
    assert client.get(f"/api/investors/{inv.id}").status_code == 401


def test_jwt_login_then_authenticated_access(client, jwt_mode, advisor, make_investor):
    inv = make_investor(owned_by=advisor)
    resp = _login(client, "adv1", "pw-one")
    assert resp.status_code == 200
    access = resp.json()["access"]
    got = client.get(f"/api/investors/{inv.id}", **_bearer(access))
    assert got.status_code == 200
    assert got.json()["id"] == inv.id


def test_jwt_bad_credentials_rejected(client, jwt_mode, advisor):
    assert _login(client, "adv1", "wrong-pw").status_code == 401


def test_jwt_refresh_mints_new_access(client, jwt_mode, advisor):
    pair = _login(client, "adv1", "pw-one").json()
    r = _post(client, "/api/auth/token/refresh", {"refresh": pair["refresh"]})
    assert r.status_code == 200
    assert "access" in r.json()


def test_jwt_refresh_rejects_garbage(client, jwt_mode, advisor):
    assert _post(client, "/api/auth/token/refresh", {"refresh": "not-a-token"}).status_code == 401


def test_cross_advisor_access_404s(client, jwt_mode, advisor, django_user_model, make_investor):
    """Advisor 2 is authenticated but doesn't own advisor 1's investor → 404."""
    inv1 = make_investor(owned_by=advisor)
    django_user_model.objects.create_user(username="adv2", password="pw-two")
    access2 = _login(client, "adv2", "pw-two").json()["access"]

    # Authenticated, but the id isn't theirs — indistinguishable from missing.
    assert client.get(f"/api/investors/{inv1.id}", **_bearer(access2)).status_code == 404
    # And advisor 2's own roster is empty (no leak of advisor 1's investors).
    assert client.get("/api/investors/", **_bearer(access2)).json() == []
