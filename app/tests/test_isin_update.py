"""Writable casparser-isin DB: seed/re-seed + the daily update + launch catch-up.

No network and no 46 MB copy — a tiny fake "bundled" DB stands in (patched onto
casparser_isin), and the casparser updater is stubbed so we test our plumbing
(seed-on-first-run, re-seed-when-bundle-newer, CASPARSER_ISIN_DB wiring, and the
stale-marker catch-up), not casparser's download.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest


def _make_db(path: Path, version: str) -> None:
    path.unlink(missing_ok=True)  # fresh DB each time (callers also overwrite)
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        con.execute("INSERT INTO meta VALUES ('version', ?)", (version,))
        con.commit()
    finally:
        con.close()


@pytest.fixture
def fake_bundle(tmp_path, monkeypatch):
    """A stand-in bundled isin.db, patched where casparser_isin exposes it."""
    bundled = tmp_path / "bundle" / "isin.db"
    bundled.parent.mkdir()
    _make_db(bundled, "2026.1.0")
    monkeypatch.setattr("casparser_isin.utils.INTERNAL_ISIN_DB_PATH", bundled)
    monkeypatch.delenv("CASPARSER_ISIN_DB", raising=False)
    yield bundled
    os.environ.pop("CASPARSER_ISIN_DB", None)


def test_ensure_seeds_and_points_env(tmp_path, settings, fake_bundle):
    target = tmp_path / "data" / "isin.db"
    settings.FOLIOMAN_ISIN_DB_PATH = target
    from folioman_app.services.isin_db import ensure_isin_db

    result = ensure_isin_db()
    assert result == target
    assert target.is_file()  # seeded from the bundle
    assert os.environ["CASPARSER_ISIN_DB"] == str(target)


def test_ensure_noop_without_path(tmp_path, settings, fake_bundle):
    settings.FOLIOMAN_ISIN_DB_PATH = None
    from folioman_app.services.isin_db import ensure_isin_db

    assert ensure_isin_db() is None
    assert "CASPARSER_ISIN_DB" not in os.environ


def test_reseeds_only_when_bundle_is_newer(tmp_path, settings, fake_bundle):
    target = tmp_path / "data" / "isin.db"
    settings.FOLIOMAN_ISIN_DB_PATH = target
    from folioman_app.services.isin_db import _db_version, ensure_isin_db

    ensure_isin_db()  # seed at 2026.1.0
    # A stale writable copy is NOT overwritten when the bundle isn't newer.
    _make_db(target, "2026.9.9")  # pretend the writable copy is ahead
    ensure_isin_db()
    assert _db_version(target) == "2026.9.9"  # untouched

    # An app update ships a newer bundle → re-seed.
    _make_db(fake_bundle, "2027.1.0")
    ensure_isin_db()
    assert _db_version(target) == "2027.1.0"


def test_update_skips_without_path(settings, fake_bundle):
    settings.FOLIOMAN_ISIN_DB_PATH = None
    from folioman_app.tasks.isin_jobs import update_isin_database

    assert update_isin_database() == 0  # nothing to update against


def test_update_runs_and_touches_marker(tmp_path, settings, fake_bundle, monkeypatch):
    target = tmp_path / "data" / "isin.db"
    settings.FOLIOMAN_ISIN_DB_PATH = target
    calls = []
    monkeypatch.setattr("casparser_isin.cli.update_isin_db", lambda: calls.append(1))
    from folioman_app.tasks.isin_jobs import update_isin_database

    assert update_isin_database() == 1
    assert calls == [1]
    assert Path(f"{target}.checked").exists()


def test_catch_up_skips_when_checked_recently(tmp_path, settings, fake_bundle, monkeypatch):
    target = tmp_path / "data" / "isin.db"
    settings.FOLIOMAN_ISIN_DB_PATH = target
    calls = []
    monkeypatch.setattr("casparser_isin.cli.update_isin_db", lambda: calls.append(1))
    marker = Path(f"{target}.checked")
    target.parent.mkdir(parents=True)
    marker.touch()  # checked just now
    from folioman_app.tasks.isin_jobs import update_isin_database_if_stale

    assert update_isin_database_if_stale() == 0  # within the 23h window → skipped
    assert calls == []

    # An old marker (yesterday) → the catch-up runs.
    old = time.time() - 24 * 60 * 60
    os.utime(marker, (old, old))
    assert update_isin_database_if_stale() == 1
    assert calls == [1]
