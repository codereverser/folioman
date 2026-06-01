"""desktop.py / server.py settings load correctly.

These import each mode module directly and inspect attributes — they do NOT
activate the settings or call django.setup(), so they run in the lean default
dev env without the server-only extras installed. A behavioural check applies
the desktop init_command to a real sqlite connection and confirms WAL.
"""

from __future__ import annotations

import importlib
import sqlite3


def test_desktop_sqlite_wal_configured():
    desktop = importlib.import_module("folioman_app.settings.desktop")
    db = desktop.DATABASES["default"]
    assert db["ENGINE"] == "django.db.backends.sqlite3"
    init = db["OPTIONS"]["init_command"]
    assert "journal_mode=WAL" in init
    assert "synchronous=NORMAL" in init
    assert "busy_timeout=5000" in init
    assert db["OPTIONS"]["transaction_mode"] == "IMMEDIATE"
    assert desktop.DEBUG is False
    # No network login on the user's own machine — single local user.
    assert desktop.FOLIOMAN_API_AUTH == "local"


def test_desktop_data_dir_env_override(monkeypatch, tmp_path):
    import folioman_app.settings.desktop as desktop

    monkeypatch.setenv("FOLIOMAN_DATA_DIR", str(tmp_path))
    try:
        importlib.reload(desktop)
        assert desktop.DATABASES["default"]["NAME"] == tmp_path / "folioman.sqlite3"
    finally:
        monkeypatch.delenv("FOLIOMAN_DATA_DIR", raising=False)
        importlib.reload(desktop)  # restore the module to its env-free default


def test_server_postgres_and_jwt_from_env(monkeypatch):
    import folioman_app.settings.base as base

    # Set env BEFORE importing server: its module-level startup guard refuses to
    # import without a real secret. base is reloaded so SECRET_KEY recomputes.
    monkeypatch.setenv("FOLIOMAN_DB_NAME", "folio_test")
    monkeypatch.setenv("FOLIOMAN_ALLOWED_HOSTS", "folioman.example.com, 10.0.0.5")
    monkeypatch.setenv("FOLIOMAN_SECRET_KEY", "x" * 50)
    importlib.reload(base)
    server = importlib.import_module("folioman_app.settings.server")
    try:
        importlib.reload(server)
        db = server.DATABASES["default"]
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["NAME"] == "folio_test"
        assert server.ALLOWED_HOSTS == ["folioman.example.com", "10.0.0.5"]
        # JWT token policy present; ninja_jwt is intentionally NOT a Django app.
        assert "ACCESS_TOKEN_LIFETIME" in server.NINJA_JWT
        assert "ninja_jwt" not in server.INSTALLED_APPS
        assert server.FOLIOMAN_API_AUTH == "jwt"  # bearer tokens required
        assert server.DEBUG is False
    finally:
        monkeypatch.delenv("FOLIOMAN_DB_NAME", raising=False)
        monkeypatch.delenv("FOLIOMAN_ALLOWED_HOSTS", raising=False)
        # Keep FOLIOMAN_SECRET_KEY set through the restoring reloads (the guard
        # would otherwise refuse to import); monkeypatch clears it at teardown.
        importlib.reload(base)
        importlib.reload(server)


def test_desktop_init_command_actually_enables_wal(tmp_path):
    """Behavioural: applying the configured PRAGMAs the way Django does yields WAL."""
    desktop = importlib.import_module("folioman_app.settings.desktop")
    init = desktop.DATABASES["default"]["OPTIONS"]["init_command"]
    conn = sqlite3.connect(tmp_path / "wal_probe.sqlite3")
    try:
        # Django's sqlite backend splits init_command on ';' and runs each.
        for statement in (s.strip() for s in init.split(";")):
            if statement:
                conn.execute(statement)
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()
