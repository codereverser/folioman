"""Server run mode: Postgres + JWT auth, configured from the environment.

Connection details and `ALLOWED_HOSTS` come from env so one image runs in any
deployment (self-hosted / Docker). Server-only deps (psycopg, django-ninja-jwt)
install via `folioman-app[server]` and are excluded from the desktop Nuitka
build.

Note on JWT: `ninja_jwt` is NOT a Django app (no AppConfig / migrations), so it
does not go in `INSTALLED_APPS`. This module only sets the token policy
(`NINJA_JWT`) that the JWT auth backend reads; the token routes + protected-route
auth are wired up at the API layer. The optional
`ninja_jwt.token_blacklist` app (token revocation) is deferred past v1.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from folioman_app._env import env
from folioman_app.settings._logging import make_logging
from folioman_app.settings.base import *
from folioman_app.settings.base import DEV_SECRET_KEY

DEBUG = False

# Comma-separated hostnames, e.g. "folioman.example.com,10.0.0.5".
ALLOWED_HOSTS = [h.strip() for h in env.list("FOLIOMAN_ALLOWED_HOSTS", []) if h.strip()]

# Postgres via a single 12-factor DATABASE_URL — required. env.dj_db_url raises at
# import when it's unset, so a misconfigured server fails closed instead of silently
# falling back. Compose builds it from the db service; Render injects it.
# CONN_MAX_AGE: persistent connections (0 = close each request); 60s is safe behind
# a pooling proxy or modest concurrency.
DATABASES = {
    "default": env.dj_db_url("DATABASE_URL", conn_max_age=env.int("FOLIOMAN_DB_CONN_MAX_AGE", 60))
}

# JWT bearer auth on every API route (api/auth.py reads this flag per-request).
# Read from env (default jwt) only so the guard below can reject an operator who
# explicitly tries to weaken it — server mode must never be "local".
FOLIOMAN_API_AUTH = env.str("FOLIOMAN_API_AUTH", "jwt")

# --- Fail-closed startup guards --------------------------------------------
# These run at settings-import time, which gunicorn does on boot — so they are a
# *guaranteed* hard failure, unlike a `manage.py check` that a deploy might skip.
#
# #1 Auth bypass: in "local" mode every request authenticates as a superuser. That
#    is correct for the desktop app but catastrophic on a network. Refuse to serve.
if FOLIOMAN_API_AUTH != "jwt":
    msg = (
        "Server mode requires FOLIOMAN_API_AUTH='jwt'. Refusing to start: 'local' "
        "mode treats every request as a superuser (desktop-only) and would expose a "
        "silent auth bypass on a networked deployment."
    )
    raise ImproperlyConfigured(msg)

# #2 Forgeable JWTs: ninja_jwt signs tokens with SECRET_KEY. If the dev fallback
#    leaks into production, anyone can mint valid tokens. Require a real key.
if not env.str("FOLIOMAN_SECRET_KEY", "") or SECRET_KEY == DEV_SECRET_KEY:
    msg = (
        "Server mode requires FOLIOMAN_SECRET_KEY to be set to a real, non-dev value "
        "(it signs JWTs). Refusing to start with the insecure dev fallback."
    )
    raise ImproperlyConfigured(msg)

# JWT token policy (django-ninja-jwt). SIGNING_KEY defaults to SECRET_KEY; the
# real key lifecycle lives in the security layer. Short access token + a week-long
# refresh keeps the mobile PWA logged in without frequent re-auth.
NINJA_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}

# Optional one-time token gating the browser first-admin setup (LAN hardening).
# The container entrypoint autogenerates one and prints it to the console on first
# boot; an operator may pin a stable value via this env var. Empty ⇒ the setup
# endpoint falls back to its zero-users gate only (dev / non-Docker runs).
FOLIOMAN_SETUP_TOKEN = env.str("FOLIOMAN_SETUP_TOKEN", "")

# Writable casparser-isin DB path (a mounted volume) so the daily updater can
# refresh it in place. Unset → use the bundled DB read-only, no auto-update.
# ensure_isin_db() (called from run_scheduler / the web entrypoint) seeds it.
_isin_db = env.str("FOLIOMAN_ISIN_DB", "")
FOLIOMAN_ISIN_DB_PATH = Path(_isin_db) if _isin_db else None

# Console always (so `docker logs` captures output); plus a rotating file when
# FOLIOMAN_LOG_DIR is set (e.g. a mounted volume). No telemetry, ever.
_log_dir = env.str("FOLIOMAN_LOG_DIR", "")
LOGGING = make_logging(
    Path(_log_dir) / "folioman.log" if _log_dir else None,
    console=True,
)

# PAN encryption key MUST come from the environment in server mode. No autogen,
# no dev fallback — a missing key fails `manage.py check` / startup (see
# security/checks.py) instead of silently failing the first PAN decrypt.
FERNET_KEY_PATH = None
FERNET_KEY_AUTOGEN = False
FERNET_KEY_REQUIRED = True
DEV_FERNET_KEY = None
