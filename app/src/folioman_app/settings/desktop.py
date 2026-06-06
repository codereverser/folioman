"""Desktop run mode: SQLite under a user-data dir, single local user, no JWT.

The desktop binary runs entirely on the user's own machine, so there
is no network auth: a single local user is created on first run
and every request is treated as that user.

SQLite is configured with WAL + a busy timeout so the app and the separate
`refresh_navs` scheduler process can both touch the database without
hitting "database is locked".

The user-data directory is `FOLIOMAN_DATA_DIR` if set, else the per-OS
platformdirs location (e.g. `~/Library/Application Support/folioman` on macOS,
`~/.local/share/folioman` on Linux, `%LOCALAPPDATA%\\folioman` on Windows). The
desktop launcher creates it on first run before `django.setup()`.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

from folioman_app.settings._logging import make_logging
from folioman_app.settings._sqlite import sqlite_concurrency_options
from folioman_app.settings.base import *

# Writable per-OS user-data dir, env-overridable. The desktop launcher sets
# FOLIOMAN_DATA_DIR to this same resolved path and mkdirs it before django.setup,
# so settings import never touches the filesystem (logging uses delay=True).
DATA_DIR = Path(os.environ.get("FOLIOMAN_DATA_DIR") or user_data_dir("folioman", "folioman"))

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

# WAL + busy_timeout + IMMEDIATE so the request thread and the in-process scheduler
# thread share this file without "database is locked" (see _sqlite.py for the why).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "folioman.sqlite3",
        "OPTIONS": sqlite_concurrency_options(),
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
