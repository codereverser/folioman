"""Daily refresh of the casparser-isin database (the writable copy).

Delegates the actual work to ``casparser_isin.cli.update_isin_db``: it version-
checks the remote metadata, and only when the remote is newer streams + sha256-
verifies the DB and atomically swaps it into place. So the daily job is a cheap
version check that downloads rarely. A marker file records the last check so the
desktop launch catch-up can tell whether a run was skipped while the app was shut.

This fetches public reference data from casparser.atomcoder.com — the whole DB in
one request, so unlike the per-fund NAV fetch it reveals nothing about holdings.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Don't re-check more than once a day on launch catch-up (the cron handles the
# regular cadence). A touch of the marker after each check drives this.
_CHECK_INTERVAL_SECONDS = 23 * 60 * 60


def _marker() -> Path | None:
    target = getattr(settings, "FOLIOMAN_ISIN_DB_PATH", None)
    return Path(f"{target}.checked") if target else None


def update_isin_database() -> int:
    """Check for and apply a newer casparser-isin DB. Returns 1 if the check ran,
    0 if skipped (no writable DB path configured — dev / bundled-only)."""
    from folioman_app.services.isin_db import ensure_isin_db

    if ensure_isin_db() is None:
        return 0
    from casparser_isin.cli import update_isin_db

    update_isin_db()  # logs its own version-check / download / no-op result
    marker = _marker()
    if marker is not None:
        marker.touch()
    return 1


def update_isin_database_if_stale() -> int:
    """Launch catch-up: run the update only when the last check is older than a day
    (or never), so a desktop run skipped while the app was closed still happens. No
    duplicate work when already checked today. Returns 1 if it ran, else 0."""
    marker = _marker()
    if (
        marker is not None
        and marker.exists()
        and (time.time() - marker.stat().st_mtime) < _CHECK_INTERVAL_SECONDS
    ):
        return 0
    return update_isin_database()
