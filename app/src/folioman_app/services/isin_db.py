"""Writable casparser-isin database: seed from the bundle + version helpers.

casparser-isin resolves its DB via ``CASPARSER_ISIN_DB`` *only if that points at an
existing file*, else the read-only bundled copy shipped in the wheel/app bundle.
The daily updater (``casparser_isin.cli.update_isin_db``) writes in place — so to
let it update, we keep a **writable** copy under the app's data dir and point the
env var at it. The bundled DB is the offline seed and fallback.

``ensure_isin_db`` is idempotent and cheap after the first seed; every ISIN job
calls it, so the scheduler/web process always has the env pointed at the writable
copy. It's a no-op (returns ``None``) when no writable path is configured (dev),
in which case casparser uses the bundled DB read-only and never auto-updates.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def _db_version(path: Path) -> str | None:
    """Read the ``version`` value from a casparser-isin DB's ``meta`` table.

    Opened read-only; any error (missing file/table) returns ``None`` so callers
    treat the version as unknown and skip the version-gated re-seed.
    """
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _is_newer(candidate: str | None, current: str | None) -> bool:
    """True if ``candidate`` is a strictly newer version than ``current``."""
    if not candidate or not current:
        return False
    from packaging import version

    try:
        return version.parse(candidate) > version.parse(current)
    except version.InvalidVersion:
        return False


def ensure_isin_db() -> Path | None:
    """Seed/refresh the writable ISIN DB copy and point ``CASPARSER_ISIN_DB`` at it.

    Copies the bundled DB to ``settings.FOLIOMAN_ISIN_DB_PATH`` on first run, and
    re-seeds when an app update ships a *newer* bundled DB than the writable copy
    (so a long-idle install doesn't start far behind). Returns the writable path,
    or ``None`` when none is configured.
    """
    target = getattr(settings, "FOLIOMAN_ISIN_DB_PATH", None)
    if not target:
        return None
    target = Path(target)

    from casparser_isin.utils import INTERNAL_ISIN_DB_PATH as bundled

    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(bundled, target)
        logger.info("isin db: seeded writable copy at %s", target)
    elif _is_newer(_db_version(bundled), _db_version(target)):
        shutil.copy2(bundled, target)
        logger.info("isin db: re-seeded from newer bundled DB")

    os.environ["CASPARSER_ISIN_DB"] = str(target)
    return target
