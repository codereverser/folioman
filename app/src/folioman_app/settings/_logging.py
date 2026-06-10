"""structlog-backed logging config shared by base.py, desktop.py and server.py.

Privacy-first: logs only ever go to local files (and optionally the console in
server mode, so `docker logs` works). There are no network / telemetry handlers,
by design.

Everything routes through ``structlog.stdlib.ProcessorFormatter``, so the
existing ``logging.getLogger(__name__)`` call sites keep working unchanged —
their records pass through the same processor chain as native structlog ones.
Two renderings of the same stream:

- **console**: ``structlog.dev.ConsoleRenderer`` — aligned, coloured (when the
  stream is a TTY), with rich-formatted tracebacks (rich is installed, so the
  renderer picks it up automatically).
- **file**: the same renderer with colours off — plain, grep-able lines for the
  size-rotating local file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import structlog

# Processors applied to records from BOTH structlog and stdlib loggers before a
# handler's renderer sees them. Timestamps are local wall-clock — these logs sit
# next to the user, not in a fleet aggregator.
_PRE_CHAIN = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.ExtraAdder(),
    structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
]

_CONFIGURED = False


def _configure_structlog() -> None:
    """Route native structlog loggers into stdlib logging (idempotent), so the
    LOGGING dict below is the single place handlers/levels are decided."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    structlog.configure(
        processors=[
            *_PRE_CHAIN,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def _formatter(*, colors: bool) -> dict[str, Any]:
    # show_locals=False: rich's default traceback dumps local variables, which
    # in this codebase can hold PAN material / portfolio data. Frame locations
    # are enough to debug; never put values in a log.
    exception_formatter = (
        structlog.dev.RichTracebackFormatter(show_locals=False)
        if colors
        else structlog.dev.plain_traceback
    )
    return {
        "()": structlog.stdlib.ProcessorFormatter,
        "processors": [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=colors, exception_formatter=exception_formatter),
        ],
        "foreign_pre_chain": _PRE_CHAIN,
    }


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
    _configure_structlog()
    handlers: dict[str, Any] = {}
    root_handlers: list[str] = []

    if log_file is not None:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_file),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "plain",
            "level": level,
            "delay": True,
        }
        root_handlers.append("file")

    if console or not root_handlers:
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "formatter": "console",
            "level": level,
        }
        root_handlers.append("console")

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            # Colour only when a human is watching — `docker logs` and file
            # redirects get clean text instead of ANSI escapes.
            "console": _formatter(colors=sys.stderr.isatty()),
            "plain": _formatter(colors=False),
        },
        "handlers": handlers,
        "root": {"handlers": root_handlers, "level": level},
        "loggers": {
            "django": {"handlers": root_handlers, "level": "INFO", "propagate": False},
        },
    }
