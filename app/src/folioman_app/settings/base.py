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

import os
from pathlib import Path

# .../app/src/folioman_app/settings/base.py -> BASE_DIR = .../folioman_app
# Runtime DB / static locations are overridden by desktop.py / server.py;
# this default keeps base.py self-sufficient for tests and `manage.py check`.
BASE_DIR = Path(__file__).resolve().parent.parent

# Named so server.py can refuse to boot if this dev fallback ever reaches a
# networked deployment (it signs JWTs — see the startup guard in server.py).
DEV_SECRET_KEY = "dev-insecure-change-me-set-a-real-key-in-production"
SECRET_KEY = os.environ.get("FOLIOMAN_SECRET_KEY", DEV_SECRET_KEY)
DEBUG = os.environ.get("FOLIOMAN_DEBUG", "0") == "1"
ALLOWED_HOSTS: list[str] = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
    "folioman_app.apps.FoliomanAppConfig",
]

# Lean middleware: this is a JSON API (Django Ninja), not a server-rendered
# site. Session/auth/CSRF middleware are intentionally omitted — server-mode auth
# is JWT. Add back deliberately if a real need appears.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# --- API auth mode (api/auth.py) -------------------------------------------
# "local": no login — every request is the single local advisor user (desktop;
#          also the test default so the suite hits the API without tokens).
# "jwt":   django-ninja-jwt bearer tokens required on every route (server.py).
# The auth backend reads this per-request, so a test can flip it via
# @override_settings without rebuilding the NinjaAPI instance.
FOLIOMAN_API_AUTH = "local"

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

# Placeholder database. desktop.py points NAME at a user-data SQLite file;
# server.py switches to Postgres. Tests use the in-memory SQLite test DB.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "folioman.sqlite3",
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

# --- PAN-at-rest encryption key (security/keys.py) -------------------------
# Resolution order: FOLIOMAN_FERNET_KEY env -> FERNET_KEY_PATH file (autogen on
# desktop) -> DEV_FERNET_KEY. Desktop/server override these in their modules.
FERNET_KEY_PATH = None
FERNET_KEY_AUTOGEN = False
FERNET_KEY_REQUIRED = False
# Dev / test fallback only — like the dev SECRET_KEY, never used in production
# (real keys come from env or the generated desktop key file). Insecure on purpose.
DEV_FERNET_KEY = os.environ.get(
    "FOLIOMAN_FERNET_KEY", "lxS4L-1mmiEwlCCHsqgXzByglZ7TWlgcV3XeG7mTmY0="
)
