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

import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from folioman_app.settings._logging import make_logging
from folioman_app.settings.base import *
from folioman_app.settings.base import DEV_SECRET_KEY

DEBUG = False

# Comma-separated hostnames, e.g. "folioman.example.com,10.0.0.5".
ALLOWED_HOSTS = [
    host.strip() for host in os.environ.get("FOLIOMAN_ALLOWED_HOSTS", "").split(",") if host.strip()
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("FOLIOMAN_DB_NAME", "folioman"),
        "USER": os.environ.get("FOLIOMAN_DB_USER", "folioman"),
        "PASSWORD": os.environ.get("FOLIOMAN_DB_PASSWORD", ""),
        "HOST": os.environ.get("FOLIOMAN_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("FOLIOMAN_DB_PORT", "5432"),
        # Persistent connections; 0 closes after each request. 60s is a safe
        # default behind a connection-pooling proxy or modest concurrency.
        "CONN_MAX_AGE": int(os.environ.get("FOLIOMAN_DB_CONN_MAX_AGE", "60")),
    }
}

# JWT bearer auth on every API route (api/auth.py reads this flag per-request).
# Read from env (default jwt) only so the guard below can reject an operator who
# explicitly tries to weaken it — server mode must never be "local".
FOLIOMAN_API_AUTH = os.environ.get("FOLIOMAN_API_AUTH", "jwt")

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
if not os.environ.get("FOLIOMAN_SECRET_KEY") or SECRET_KEY == DEV_SECRET_KEY:
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

# Console always (so `docker logs` captures output); plus a rotating file when
# FOLIOMAN_LOG_DIR is set (e.g. a mounted volume). No telemetry, ever.
_log_dir = os.environ.get("FOLIOMAN_LOG_DIR")
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
