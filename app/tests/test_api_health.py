"""Health probe: unauthenticated, DB-aware, 200 when reachable / 503 when not."""

from __future__ import annotations

import pytest
from folioman_app.api import health


@pytest.mark.django_db
def test_health_ok_when_database_reachable(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "database": "ok"}


def test_health_503_when_database_unreachable(client, monkeypatch):
    # Simulate a process that is up but cannot reach the database.
    monkeypatch.setattr(health, "_database_ok", lambda: False)
    resp = client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "error", "database": "unavailable"}


def test_health_is_in_openapi(client):
    assert "/api/health" in client.get("/api/openapi.json").json()["paths"]
