"""Fernet key resolution for PAN-at-rest encryption.

Resolution order:
  1. ``FOLIOMAN_FERNET_KEY`` env var — server / CI / any explicit override.
  2. ``settings.FERNET_KEY_PATH`` file — desktop; auto-generated on first run
     when ``settings.FERNET_KEY_AUTOGEN`` is True, written with 0600 perms.
  3. ``settings.DEV_FERNET_KEY`` — tests / local only (insecure, clearly labelled).

Losing the key makes existing PANs unrecoverable — by design, they are encrypted
at rest. Rotation is manual in v1: decrypt-all with the old key, re-encrypt with
the new (documented, not automated).
"""

from __future__ import annotations

import stat
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings

from folioman_app._env import env


class FernetKeyUnavailable(RuntimeError):
    """No Fernet key could be resolved from any configured location."""


def _generate_key_file(path: Path) -> bytes:
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600 — owner read/write only
    return key


def resolve_fernet_key() -> bytes:
    """Return the raw Fernet key bytes, or raise ``FernetKeyUnavailable``."""
    env_key = env.str("FOLIOMAN_FERNET_KEY", "")
    if env_key:
        return env_key.encode()

    key_path = getattr(settings, "FERNET_KEY_PATH", None)
    if key_path:
        path = Path(key_path)
        if path.exists():
            return path.read_bytes().strip()
        if getattr(settings, "FERNET_KEY_AUTOGEN", False):
            return _generate_key_file(path)

    dev_key = getattr(settings, "DEV_FERNET_KEY", None)
    if dev_key:
        return dev_key.encode() if isinstance(dev_key, str) else dev_key

    raise FernetKeyUnavailable(
        "No PAN encryption key available. Set FOLIOMAN_FERNET_KEY (server), or "
        "enable FERNET_KEY_AUTOGEN with FERNET_KEY_PATH (desktop)."
    )


@lru_cache(maxsize=1)
def get_key_bytes() -> bytes:
    """Cached raw key — backs both Fernet encryption and the PAN lookup HMAC."""
    return resolve_fernet_key()


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    return Fernet(get_key_bytes())
