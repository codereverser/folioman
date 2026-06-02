"""Single-origin serving: Django returns the built SPA for client-side routes
while /api stays the JSON API."""

from __future__ import annotations

import pytest
from django.test import override_settings

INDEX_HTML = '<!doctype html><html><body><div id="app"></div></body></html>'


def _write_dist(tmp_path):
    (tmp_path / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    return str(tmp_path)


def test_spa_fallback_serves_index_for_a_deep_link(client, tmp_path):
    with override_settings(FRONTEND_DIST=_write_dist(tmp_path)):
        resp = client.get("/investors/5/dashboard")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/html")
    body = b"".join(resp.streaming_content)
    assert b'<div id="app">' in body


def test_spa_fallback_503_when_not_built(client, tmp_path):
    # tmp_path has no index.html → the frontend isn't built.
    with override_settings(FRONTEND_DIST=str(tmp_path)):
        resp = client.get("/dashboard")
    assert resp.status_code == 503
    assert b"frontend-build" in resp.content.lower()


@pytest.mark.django_db
def test_api_is_not_shadowed_by_the_spa(client, tmp_path):
    # An /api route stays JSON even with the SPA fallback registered.
    with override_settings(FRONTEND_DIST=_write_dist(tmp_path)):
        resp = client.get("/api/investors/")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("application/json")
