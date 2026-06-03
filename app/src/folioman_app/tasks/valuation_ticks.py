"""Scheduler-neutral entrypoints for the day-wise valuation ticks.

This is the stable, broker-free contract that *any* trigger calls — the
in-process APScheduler (desktop), the dedicated ``run_scheduler`` process
(server), or an external scheduler (OS cron, systemd timer, Kubernetes CronJob,
a Celery Beat shell task) invoking the one-shot management commands. The trigger
is therefore a replaceable convention, not a class hierarchy: the jobs are plain
idempotent functions on the ``Investor`` DB work-list, and these ticks are the
thin, side-effect-contained wrappers around them.

Each tick owns connection hygiene (APScheduler reuses worker threads, so stale
per-thread connections must be closed) and never lets a job exception escape to
kill a long-lived scheduler. Keep this module free of any ``apscheduler`` (or
broker) import so it stays usable from a bare ``manage.py`` process and inside
the pure-Python desktop build.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.db import close_old_connections

logger = logging.getLogger(__name__)


def _run(fn: Callable[[], int]) -> int:
    """Close stale per-thread DB connections around a job run and contain any
    exception (return 0 on failure) so a recurring trigger keeps ticking."""
    close_old_connections()
    try:
        return fn()
    except Exception:
        logger.exception("valuation tick %s failed", getattr(fn, "__name__", fn))
        return 0
    finally:
        close_old_connections()


def run_pending_valuations_tick() -> int:
    """Process every investor whose valuation is pending/computing or a
    due-for-retry error. Returns how many were processed."""
    from folioman_app.tasks.valuation_jobs import process_pending_valuations

    return _run(process_pending_valuations)


def run_daily_extend_tick() -> int:
    """Roll every ready series forward to today and re-queue errored investors.
    Returns how many were queued."""
    from folioman_app.tasks.valuation_jobs import enqueue_daily_extend

    return _run(enqueue_daily_extend)
