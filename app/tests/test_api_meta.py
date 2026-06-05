"""App metadata endpoint: version + data location for the Settings screen."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_meta_reports_version_and_local_sqlite_location(client):
    body = client.get("/api/meta").json()
    assert body["version"]  # non-empty version string
    # The test suite runs on SQLite, so it reads as a local-storage build with a
    # concrete database path the Settings page can show for backup guidance.
    assert body["storage"] == "local"
    assert body["data_location"]  # a concrete location string (a path / DB URI)


def test_meta_is_in_openapi(client):
    assert "/api/meta" in client.get("/api/openapi.json").json()["paths"]
