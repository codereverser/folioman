"""Startup guard: refuse to run without a PAN encryption key when one is required.

Registered in ``AppConfig.ready``. Server mode sets ``FERNET_KEY_REQUIRED=True``,
so a missing ``FOLIOMAN_FERNET_KEY`` fails ``manage.py check`` / ``runserver``
loudly instead of silently failing the first PAN decrypt. Desktop auto-generates
the key (never blocks a fresh install) and tests use a dev fallback, so neither
sets the flag.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import Error, Tags, register

from folioman_app.security.keys import FernetKeyUnavailable, resolve_fernet_key


@register(Tags.security)
def check_pan_encryption_key(app_configs: Any, **kwargs: Any) -> list[Error]:
    if not getattr(settings, "FERNET_KEY_REQUIRED", False):
        return []
    try:
        resolve_fernet_key()
    except FernetKeyUnavailable as exc:
        return [
            Error(
                str(exc),
                hint="Set FOLIOMAN_FERNET_KEY (cryptography.fernet.Fernet.generate_key()).",
                id="folioman_app.security.E001",
            )
        ]
    return []
