"""Rotating-file logging config shared by desktop.py and server.py.

Privacy-first: logs only ever go to local files (and optionally the console in
server mode, so `docker logs` works). There are no network / telemetry handlers,
by design.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def make_logging(
    log_file: Path | None = None,
    *,
    console: bool = False,
    level: str = "INFO",
) -> dict[str, Any]:
    """Build a Django ``LOGGING`` dict.

    Writes to a size-rotating local file when ``log_file`` is given, and/or to
    the console when ``console`` is true (a console handler is always added if
    nothing else would be, so logging is never silently a no-op).

    ``delay=True`` on the file handler defers opening the file until the first
    record, so importing settings / running ``manage.py check`` never touches
    the filesystem — the user-data dir may not exist until first-run bootstrap.
    """
    handlers: dict[str, Any] = {}
    root_handlers: list[str] = []

    if log_file is not None:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_file),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "verbose",
            "level": level,
            "delay": True,
        }
        root_handlers.append("file")

    if console or not root_handlers:
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "level": level,
        }
        root_handlers.append("console")

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "verbose": {
                "format": "{asctime} {levelname} {name}: {message}",
                "style": "{",
            }
        },
        "handlers": handlers,
        "root": {"handlers": root_handlers, "level": level},
        "loggers": {
            "django": {"handlers": root_handlers, "level": "INFO", "propagate": False},
        },
    }
