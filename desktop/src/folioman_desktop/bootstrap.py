"""First-run bootstrap for the desktop binary.

The packaged app ships with no database: the first launch must stand up a
working, migrated, empty install under a writable per-OS user-data dir, then
every later launch must detect that existing state and skip straight to serving.

Order matters and is enforced here:

1. Resolve + create the user-data dir (and ``logs/``) **before** ``django.setup``,
   so the rotating log handler can open its file and settings import never has to
   touch a directory that may not exist yet.
2. Defer the in-process scheduler (env flag read by ``apps.py``) until after the
   DB is migrated — otherwise the launch catch-up tick would query missing tables
   on a fresh install.
3. ``django.setup()`` → migrate-if-behind → create the single local user →
   materialise the PAN-encryption Fernet key.

Every step is idempotent: a second launch finds the DB at the right migration,
the user present, and the key file on disk, and does no work.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = "folioman_app.settings.desktop"


def ensure_settings_module() -> None:
    """Point Django at the desktop settings unless the env already overrides it."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", DEFAULT_SETTINGS)


def _point_at_bundled_spa() -> None:
    """In a packaged build the SPA is bundled next to this module; point Django at it.

    The Nuitka spec bundles ``frontend/dist`` as ``folioman_desktop/frontend_dist``,
    so it resolves from ``__file__`` in both standalone and onefile modes. A no-op
    in dev (no such dir) — settings then falls back to the repo's ``frontend/dist``.
    Honours an explicit ``FOLIOMAN_FRONTEND_DIST`` override.
    """
    if os.environ.get("FOLIOMAN_FRONTEND_DIST"):
        return
    bundled = Path(__file__).resolve().parent / "frontend_dist"
    if bundled.is_dir():
        os.environ["FOLIOMAN_FRONTEND_DIST"] = str(bundled)


def resolve_data_dir() -> Path:
    """Resolve the writable user-data dir and create it (plus ``logs/``).

    Honours ``FOLIOMAN_DATA_DIR``; otherwise the per-OS platformdirs location.
    Pins the result back into the env so ``settings.desktop`` resolves to the exact
    same path we just created — no second, divergent resolution at settings import.
    """
    from platformdirs import user_data_dir

    data_dir = Path(os.environ.get("FOLIOMAN_DATA_DIR") or user_data_dir("folioman", "folioman"))
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    # WhiteNoise scans STATIC_ROOT (settings.desktop → data_dir/staticfiles) at
    # startup and warns if it's missing; create it so the scan is quiet. v1 serves
    # nothing from /static/, so it stays empty unless a future build collects into it.
    (data_dir / "staticfiles").mkdir(parents=True, exist_ok=True)
    os.environ["FOLIOMAN_DATA_DIR"] = str(data_dir)
    return data_dir


def _migrate_if_behind() -> None:
    """Run migrations only when the DB is new or behind — keeps relaunch cheap."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    if executor.migration_plan(targets):  # empty plan → already at head
        from django.core.management import call_command

        logger.info("desktop bootstrap: applying database migrations")
        call_command("migrate", verbosity=0, interactive=False)


def _ensure_local_user() -> None:
    """Create the single local advisor user (get-or-create; idempotent)."""
    from folioman_app.api.auth import get_local_user

    get_local_user()


def _ensure_fernet_key() -> None:
    """Materialise the PAN-encryption key (autogen writes a 0600 file on first run)."""
    from folioman_app.security.keys import resolve_fernet_key

    resolve_fernet_key()


def bootstrap() -> Path:
    """Stand up (or verify) the local install. Returns the resolved data dir.

    Safe to call on every launch — does real work only the first time.
    """
    ensure_settings_module()
    # The launcher starts the scheduler itself, after migrate (see apps.py).
    os.environ.setdefault("FOLIOMAN_DEFER_SCHEDULER", "1")
    _point_at_bundled_spa()
    data_dir = resolve_data_dir()

    import django

    django.setup()

    _migrate_if_behind()
    _ensure_local_user()
    _ensure_fernet_key()
    logger.info("desktop bootstrap complete (data dir: %s)", data_dir)
    return data_dir
