"""Server-mode fail-closed startup guards (#1 auth bypass, #2 forgeable JWT).

The guards live at settings-import time so gunicorn cannot boot insecurely (a
`manage.py check` could be skipped; an import cannot). We exercise them in a
subprocess because importing the settings module mutates process-wide state.
"""

from __future__ import annotations

import os
import subprocess
import sys

_REAL_KEY = "x" * 50  # a plausible non-dev secret


def _import_server(env_overrides: dict[str, str | None]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
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
