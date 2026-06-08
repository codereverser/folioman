"""Scheduler-neutral entrypoints for the casparser-isin database refresh.

Same contract as ``valuation_ticks``: thin, exception-contained wrappers any
trigger can call — the in-process APScheduler (desktop), the ``run_scheduler``
process (server), or an external scheduler via the ``update_isin_db`` management
command. No ``apscheduler`` import, so it stays usable from a bare ``manage.py``
process and the pure-Python desktop build. The jobs touch no Django DB, so unlike
the valuation ticks there's no connection hygiene to do here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _run(fn: Callable[[], int]) -> int:
    """Contain any exception (return 0) so a recurring trigger keeps ticking."""
    try:
        return fn()
    except Exception:
        logger.exception("isin tick %s failed", getattr(fn, "__name__", fn))
        return 0


def run_isin_update_tick() -> int:
    """Daily: fetch a newer casparser-isin DB if one is available (else a no-op)."""
    from folioman_app.tasks.isin_jobs import update_isin_database

    return _run(update_isin_database)


def run_isin_catch_up_tick() -> int:
    """Launch catch-up: refresh the ISIN DB if it hasn't been checked in ~a day —
    covers a daily run skipped while the (desktop) app was shut down."""
    from folioman_app.tasks.isin_jobs import update_isin_database_if_stale

    return _run(update_isin_database_if_stale)
