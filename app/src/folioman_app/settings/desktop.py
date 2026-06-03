"""Desktop run mode: SQLite under a user-data dir, single local user, no JWT.

The desktop binary runs entirely on the user's own machine, so there
is no network auth: a single local user is created on first run
and every request is treated as that user.

SQLite is configured with WAL + a busy timeout so the app and the separate
`refresh_navs` scheduler process can both touch the database without
hitting "database is locked".

The user-data directory is env-overridable now (`FOLIOMAN_DATA_DIR`); the
per-OS resolution via `platformdirs` and first-run directory creation arrive
later.
"""

from __future__ import annotations

import os
from pathlib import Path

from folioman_app.settings._logging import make_logging
from folioman_app.settings.base import *

# Writable user-data dir. A later change replaces this fallback with a platformdirs-based,
# per-OS location and creates it on first launch.
DATA_DIR = Path(os.environ.get("FOLIOMAN_DATA_DIR") or Path.home() / ".folioman")

# Desktop ships as a built binary; never run with DEBUG on.
DEBUG = False

# Single local advisor user, no login — the binary runs on the advisor's own
# machine (api/auth.py creates the user on first request). Inherited from base,
# pinned here so the run mode is explicit and never accidentally JWT.
FOLIOMAN_API_AUTH = "local"

# The single PyWebView process runs the valuation scheduler in-process (a daemon
# thread) — no separate worker to supervise. SQLite WAL (below) lets the scheduler
# and the request thread share the DB.
FOLIOMAN_RUN_SCHEDULER = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "folioman.sqlite3",
        "OPTIONS": {
            # Django 5.2 splits init_command on ';' and runs each PRAGMA per
            # connection. WAL lets the scheduler process read/write concurrently
            # with the app; synchronous=NORMAL is the safe+fast pairing under
            # WAL; busy_timeout makes a writer wait out a lock instead of erroring.
            "init_command": (
                "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=5000;"
            ),
            # IMMEDIATE takes the write lock at BEGIN, avoiding the deferred-then-
            # upgrade deadlock window between two concurrent writers.
            "transaction_mode": "IMMEDIATE",
        },
    }
}

# Local file logging only (no console — desktop runs as a windowed app); the
# data dir is ensured to exist before the first log record is written.
LOGGING = make_logging(DATA_DIR / "logs" / "folioman.log")

# PAN encryption key: auto-generate (0600) under the user-data dir on first run.
# No dev fallback in real desktop mode — the generated key is authoritative.
FERNET_KEY_PATH = DATA_DIR / "fernet.key"
FERNET_KEY_AUTOGEN = True
DEV_FERNET_KEY = None
