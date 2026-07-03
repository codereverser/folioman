"""Server-mode fail-closed startup guards (#1 auth bypass, #2 forgeable JWT).

The guards live at settings-import time so gunicorn cannot boot insecurely (a
`manage.py check` could be skipped; an import cannot). We exercise them in a
subprocess because importing the settings module mutates process-wide state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_REAL_KEY = "x" * 50  # a plausible non-dev secret
_DB_URL = "postgres://u:p@localhost:5432/folioman"  # valid so the DB step passes


def _import_server(env_overrides: dict[str, str | None]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("DATABASE_URL", _DB_URL)  # present by default so guards are reached
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, "-c", "import folioman_app.settings.server"],
        capture_output=True,
        text=True,
        env=env,
    )


def test_server_refuses_to_boot_without_database_url():
    result = _import_server({"FOLIOMAN_SECRET_KEY": _REAL_KEY, "DATABASE_URL": None})
    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr


def test_server_settings_boot_with_real_key_and_default_auth():
    result = _import_server({"FOLIOMAN_SECRET_KEY": _REAL_KEY, "FOLIOMAN_API_AUTH": None})
    assert result.returncode == 0, result.stderr


def test_server_refuses_to_boot_without_secret_key():
    result = _import_server({"FOLIOMAN_SECRET_KEY": None})
    assert result.returncode != 0
    assert "FOLIOMAN_SECRET_KEY" in result.stderr


def test_server_refuses_to_boot_with_dev_secret_key():
    result = _import_server(
        {"FOLIOMAN_SECRET_KEY": "dev-insecure-change-me-set-a-real-key-in-production"}
    )
    assert result.returncode != 0
    assert "FOLIOMAN_SECRET_KEY" in result.stderr


def test_server_refuses_to_boot_with_local_auth():
    result = _import_server({"FOLIOMAN_SECRET_KEY": _REAL_KEY, "FOLIOMAN_API_AUTH": "local"})
    assert result.returncode != 0
    assert "FOLIOMAN_API_AUTH" in result.stderr


def _server_default_db(env_overrides: dict[str, str | None]) -> dict[str, str]:
    """Import server settings in a subprocess and return DATABASES['default']."""
    env = os.environ.copy()
    env["FOLIOMAN_SECRET_KEY"] = _REAL_KEY  # clear the fail-closed guard
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    script = (
        "import json, folioman_app.settings.server as s;"
        "print(json.dumps({k: str(v) for k, v in s.DATABASES['default'].items()}))"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_database_url_drives_server_db_config():
    db = _server_default_db({"DATABASE_URL": "postgres://u:p@dbhost:6543/mydb"})
    assert db["HOST"] == "dbhost"
    assert db["NAME"] == "mydb"
    assert db["USER"] == "u"
    assert db["PORT"] == "6543"
    assert "postgresql" in db["ENGINE"]
    assert db["CONN_MAX_AGE"] == "60"


def test_conn_max_age_override():
    db = _server_default_db(
        {"DATABASE_URL": "postgres://u:p@dbhost:5432/mydb", "FOLIOMAN_DB_CONN_MAX_AGE": "0"}
    )
    assert db["CONN_MAX_AGE"] == "0"
