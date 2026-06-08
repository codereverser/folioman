"""First-run admin setup (server mode only, zero-users gated)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def _post(client, url, payload):
    return client.post(url, data=payload, content_type="application/json")


@pytest.fixture
def jwt_mode(_local_auth_mode, settings):
    """Flip to jwt (server) auth; skip if the server-only dep is absent."""
    pytest.importorskip("ninja_jwt")
    settings.FOLIOMAN_API_AUTH = "jwt"


# --- setup state ------------------------------------------------------------


def test_state_never_needs_admin_in_local_mode(client):
    # Local/desktop mode: there's always the local user; setup never applies.
    assert client.get("/api/setup/state").json()["needs_admin"] is False


def test_state_needs_admin_in_server_mode_with_no_users(client, jwt_mode):
    assert client.get("/api/setup/state").json()["needs_admin"] is True


def test_state_stops_needing_admin_once_a_user_exists(client, jwt_mode, django_user_model):
    django_user_model.objects.create_user(username="someone", password="pw-123456")
    assert client.get("/api/setup/state").json()["needs_admin"] is False


# --- create first admin -----------------------------------------------------


def test_create_first_admin_returns_tokens_and_makes_a_superuser(
    client, jwt_mode, django_user_model
):
    resp = _post(client, "/api/setup/admin", {"username": "boss", "password": "s3cret-pw"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access"] and body["refresh"]
    user = django_user_model.objects.get(username="boss")
    assert user.is_superuser and user.is_staff


def test_create_refuses_in_local_mode(client):
    # No login in local mode — the endpoint is inert (404).
    resp = _post(client, "/api/setup/admin", {"username": "x", "password": "s3cret-pw"})
    assert resp.status_code == 404


def test_create_refuses_once_setup_is_done(client, jwt_mode, django_user_model):
    django_user_model.objects.create_user(username="first", password="pw-123456")
    resp = _post(client, "/api/setup/admin", {"username": "second", "password": "s3cret-pw"})
    assert resp.status_code == 409


def test_create_rejects_a_too_short_password(client, jwt_mode):
    resp = _post(client, "/api/setup/admin", {"username": "boss", "password": "short"})
    assert resp.status_code == 422


# --- console setup token (9.9) ----------------------------------------------


def test_state_reports_token_required_when_configured(client, jwt_mode, settings):
    settings.FOLIOMAN_SETUP_TOKEN = "console-tok"
    body = client.get("/api/setup/state").json()
    assert body["needs_admin"] is True
    assert body["token_required"] is True


def test_state_token_not_required_without_a_configured_token(client, jwt_mode):
    assert client.get("/api/setup/state").json()["token_required"] is False


def test_create_requires_matching_token_when_configured(
    client, jwt_mode, settings, django_user_model
):
    settings.FOLIOMAN_SETUP_TOKEN = "console-tok"
    base = {"username": "boss", "password": "s3cret-pw"}

    assert _post(client, "/api/setup/admin", base).status_code == 401  # missing token
    assert _post(client, "/api/setup/admin", {**base, "token": "wrong"}).status_code == 401
    assert django_user_model.objects.count() == 0  # nothing created on a bad token

    ok = _post(client, "/api/setup/admin", {**base, "token": "console-tok"})
    assert ok.status_code == 200
    assert django_user_model.objects.get(username="boss").is_superuser


def test_setup_routes_are_in_openapi(client):
    paths = client.get("/api/openapi.json").json()["paths"]
    assert "/api/setup/state" in paths
    assert "/api/setup/admin" in paths
