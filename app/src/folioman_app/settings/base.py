"""Shared Django settings for Folioman.

`base.py` holds settings common to both run modes. These layer on top:
- `desktop.py` — SQLite under an OS user-data dir, no auth, single local user.
- `server.py` — Postgres, JWT auth, `ALLOWED_HOSTS` from env.

`base.py` is directly usable for the test suite (pytest-django uses Django's
in-memory SQLite test database). No secrets are hardcoded: `SECRET_KEY` reads
from the environment with an obvious dev fallback. The real secret lifecycle
(Fernet key for PAN encryption, ed25519 license keypair) lives in the
security layer.

Privacy-first posture: no telemetry, no phone-home, no third-party analytics
apps. Logging (local files) is configured per run mode.
"""

from __future__ import annotations

from pathlib import Path

from folioman_app._env import env
from folioman_app.settings._logging import make_logging
from folioman_app.settings._sqlite import sqlite_concurrency_options

# .../app/src/folioman_app/settings/base.py -> BASE_DIR = .../folioman_app
# Runtime DB / static locations are overridden by desktop.py / server.py;
# this default keeps base.py self-sufficient for tests and `manage.py check`.
BASE_DIR = Path(__file__).resolve().parent.parent

# Named so server.py can refuse to boot if this dev fallback ever reaches a
# networked deployment (it signs JWTs — see the startup guard in server.py).
DEV_SECRET_KEY = "dev-insecure-change-me-set-a-real-key-in-production"
SECRET_KEY = env.str("FOLIOMAN_SECRET_KEY", DEV_SECRET_KEY)
DEBUG = env.bool("FOLIOMAN_DEBUG", False)
ALLOWED_HOSTS: list[str] = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
    "folioman_app.apps.FoliomanAppConfig",
]

# Lean middleware: this is a JSON API (Django Ninja), not a server-rendered
# site. Session/auth/CSRF middleware are intentionally omitted — server-mode auth
# is JWT. WhiteNoise (right after SecurityMiddleware, per its docs) serves the
# built SPA's hashed assets from one origin alongside the API.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    # Enforces read-only mode for the public hosted demo (no-op unless DEMO_MODE).
    "folioman_app.middleware.DemoReadOnlyMiddleware",
]

# --- API auth mode (api/auth.py) -------------------------------------------
# "local": no login — every request is the single local advisor user (desktop;
#          also the test default so the suite hits the API without tokens).
# "jwt":   django-ninja-jwt bearer tokens required on every route (server.py).
# The auth backend reads this per-request, so a test can flip it via
# @override_settings without rebuilding the NinjaAPI instance.
FOLIOMAN_API_AUTH = "local"

# --- feature flags ----------------------------------------------------------
# Manual transaction authoring (POST /api/investors/{id}/transactions). The
# first release imports mutual funds via CAS PDF and equities as eCAS snapshots
# only; hand-entering transactions ships in the multi-asset release. The
# endpoint and create_manual_transaction() stay in the tree — flip this on
# (FOLIOMAN_MANUAL_TXNS=1) to re-enable, no code change needed.
MANUAL_TRANSACTIONS_ENABLED = env.bool("FOLIOMAN_MANUAL_TXNS", False)

# Hosted-demo read-only mode. When on (FOLIOMAN_DEMO=1), DemoReadOnlyMiddleware
# refuses every state-changing API request with 403 — server-side, so it holds no
# matter what the frontend exposes. Off everywhere by default; the demo deployment
# (and `seed_demo`-backed stacks) flip it on. Auth-token routes stay open so the
# JWT demo can still issue read tokens.
DEMO_MODE = env.bool("FOLIOMAN_DEMO", False)

# Day-wise valuation scheduler. Off by default (base/server) — the server runs one
# dedicated `manage.py run_scheduler` process, never one per gunicorn worker. The
# desktop settings flip it on so the single PyWebView process runs it in a thread.
# Env override (FOLIOMAN_RUN_SCHEDULER=1) for a dev runserver that wants it inline.
FOLIOMAN_RUN_SCHEDULER = env.bool("FOLIOMAN_RUN_SCHEDULER", False)

# Dev / test logging: console only, so a bare `manage.py runserver` (the dev
# loop) actually shows scheduler + valuation job logs. desktop.py / server.py
# override this with their rotating-file configs.
LOGGING = make_logging(console=True, level=env.str("FOLIOMAN_LOG_LEVEL", "INFO"))

ROOT_URLCONF = "folioman_app.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

WSGI_APPLICATION = "folioman_app.wsgi.application"

# Product version surfaced in the UI (Settings → About). Single source of truth;
# the desktop/server shells can override via the FOLIOMAN_VERSION env var.
FOLIOMAN_VERSION = env.str("FOLIOMAN_VERSION", "1.0.0")

# Placeholder database. desktop.py points NAME at a user-data SQLite file;
# server.py switches to Postgres. Tests use the in-memory SQLite test DB.
# The WAL/busy_timeout/IMMEDIATE OPTIONS matter the moment a `runserver` also runs
# the in-process scheduler (FOLIOMAN_RUN_SCHEDULER=1): request and scheduler threads
# then share this file, and these keep contention from erroring out. SQLite ignores
# WAL for the in-memory test DB, so they're a no-op under pytest.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "folioman.sqlite3",
        "OPTIONS": sqlite_concurrency_options(),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Indian investors: report and display in IST; store timezone-aware.
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Built SPA (single-origin serving) -------------------------------------
# The Vue build lands in <repo>/frontend/dist. In production WhiteNoise serves
# its hashed assets (and index.html at /) from that directory, and the SPA
# fallback in urls.py returns index.html for client-side deep links. In dev the
# Vite server hosts the SPA and proxies /api here, so this is unused.
# BASE_DIR = .../app/src/folioman_app → repo root is three parents up. A packaged
# desktop build sets FOLIOMAN_FRONTEND_DIST explicitly.
_REPO_ROOT = BASE_DIR.parent.parent.parent
FRONTEND_DIST = env.str("FOLIOMAN_FRONTEND_DIST", "") or str(_REPO_ROOT / "frontend" / "dist")

if Path(FRONTEND_DIST).is_dir():
    # Serve dist/ contents at the site root: /assets/*, /sw.js, /manifest, and
    # index.html at /. Hashed asset filenames get long-lived immutable caching.
    WHITENOISE_ROOT = FRONTEND_DIST
    WHITENOISE_INDEX_FILE = True

    def _spa_shell_headers(headers, path, url):
        # The service worker and app shell must always be revalidated. A CDN or
        # browser that caches sw.js keeps serving a stale worker after a redeploy,
        # so the PWA's update check never sees the new build and the "new version"
        # prompt never fires. no-cache (revalidate via ETag) keeps them current
        # while the hashed /assets/* stay immutable. The SPA-fallback view in
        # urls.py sets the same header for deep-link index.html responses.
        if Path(path).name in ("sw.js", "index.html"):
            headers["Cache-Control"] = "no-cache"

    WHITENOISE_ADD_HEADERS_FUNCTION = _spa_shell_headers

# --- PAN-at-rest encryption key (security/keys.py) -------------------------
# Resolution order: FOLIOMAN_FERNET_KEY env -> FERNET_KEY_PATH file (autogen on
# desktop) -> DEV_FERNET_KEY. Desktop/server override these in their modules.
FERNET_KEY_PATH = None
FERNET_KEY_AUTOGEN = False
FERNET_KEY_REQUIRED = False
# Dev / test fallback only — like the dev SECRET_KEY, never used in production
# (real keys come from env or the generated desktop key file). Insecure on purpose.
DEV_FERNET_KEY = env.str("FOLIOMAN_FERNET_KEY", "lxS4L-1mmiEwlCCHsqgXzByglZ7TWlgcV3XeG7mTmY0=")

# --- CAS/eCAS upload size cap ----------------------------------------------
# A real CAS/eCAS PDF is well under a megabyte; this cap exists so a hostile or
# accidental multi-GB upload can't be read into memory and OOM the server. The
# whole file is loaded (file.read()) to parse, so this is the load-bearing guard
# — Django's DATA_UPLOAD_MAX_MEMORY_SIZE does not bound multipart file parts.
MAX_UPLOAD_BYTES = env.int("FOLIOMAN_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)

# --- casparser-isin database -----------------------------------------------
# Writable location for the ISIN/AMFI reference DB so the daily updater can refresh
# it in place (the bundled copy is read-only). `None` (dev default) → use the
# bundled DB read-only, no auto-update. Desktop/server set a real path; the seed +
# CASPARSER_ISIN_DB env wiring is `services/isin_db.ensure_isin_db()`.
FOLIOMAN_ISIN_DB_PATH = None
