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


def test_meta_exposes_fernet_key_path_in_local_mode(client, settings):
    # Desktop surfaces the key file path so the user knows the second file to back
    # up. Base settings have no key path, so set one for this check.
    settings.FERNET_KEY_PATH = "/data/folioman/fernet.key"
    body = client.get("/api/meta").json()
    assert body["key_location"] == "/data/folioman/fernet.key"


def test_meta_hides_key_path_when_unset(client, settings):
    settings.FERNET_KEY_PATH = None
    assert client.get("/api/meta").json()["key_location"] == ""


def test_meta_is_in_openapi(client):
    assert "/api/meta" in client.get("/api/openapi.json").json()["paths"]
