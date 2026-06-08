"""APScheduler adapter for the day-wise valuation ticks.

APScheduler is **only the clock** here — one trigger provider among
interchangeable callers. The durable work-list is the ``Investor`` rows
themselves (see ``tasks/valuation_jobs.py``), and the actual job bodies are the
scheduler-neutral entrypoints in ``tasks/valuation_ticks.py``. This module just
translates a small in-process job registry into APScheduler jobs and owns the
APScheduler-specific concerns (interval/cron timing, ``max_instances``,
``coalesce``, timezone, lifecycle). An in-memory jobstore is fine — the schedule
is re-registered on each start and no job state needs to survive.

Pure-Python APScheduler (thread-based) — no Celery/RQ, no Redis, Nuitka-safe.

Hosting (same ticks both modes):
- **desktop:** ``start_background_scheduler()`` runs in-process (a daemon thread)
  via ``AppConfig.ready`` when ``FOLIOMAN_RUN_SCHEDULER`` is on.
- **server:** one dedicated ``manage.py run_scheduler`` process
  (``run_blocking_scheduler``) — never per gunicorn worker.

To move scheduling off APScheduler later (OS cron, systemd timer, k8s CronJob,
Celery Beat), point that scheduler at the ``manage.py valuation_tick_pending`` /
``valuation_tick_daily_extend`` commands instead of running this adapter — the
tick functions are unchanged. Run exactly one trigger source per environment.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from django.conf import settings

from folioman_app.tasks.isin_ticks import run_isin_catch_up_tick, run_isin_update_tick
from folioman_app.tasks.valuation_ticks import (
    run_catch_up_tick,
    run_daily_extend_tick,
    run_pending_valuations_tick,
)

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 30

# Revalue every 6 hours from 02:00 (02/08/14/20). Most MF NAVs post just after
# midnight, but some only mid-morning (~09:00), so a single 02:00 run misses them
# for the day; the later runs re-fetch and re-do the current day to pick them up.
_REVALUE_HOURS = "2,8,14,20"
# How late a revalue tick may fire and still run. APScheduler's in-memory jobstore
# has no record of runs missed while no scheduler existed, so a cold start (desktop
# closed) is covered by the launch catch-up below; this grace covers the "process
# alive but asleep across the scheduled hour" case — a machine that wakes a few
# hours late still fires the missed run (coalesced to once). Kept under the 6h gap
# so a missed run never collides with the next one.
_DAILY_MISFIRE_GRACE_SECONDS = 4 * 60 * 60


@dataclass(frozen=True)
class _Job:
    """A scheduler-neutral job definition. ``trigger`` is an APScheduler trigger
    kind ("interval"/"cron") and ``trigger_args`` its keyword arguments; the
    callable is one of the tick entrypoints (already connection-safe and
    exception-contained), so the adapter stays a thin translation layer.
    ``misfire_grace_time`` overrides APScheduler's default when set (the daily cron
    wants a wide grace so a late-waking process still fires the missed run)."""

    id: str
    func: Callable[[], int]
    trigger: str
    trigger_args: dict
    misfire_grace_time: int | None = None


# The schedule, as data. Frequent tick recomputes pending/retryable investors;
# the 6-hourly tick re-fetches NAVs and rolls every series forward, re-queuing errors.
_JOBS: tuple[_Job, ...] = (
    _Job(
        id="process_pending_valuations",
        func=run_pending_valuations_tick,
        trigger="interval",
        trigger_args={"seconds": _INTERVAL_SECONDS},
    ),
    _Job(
        id="enqueue_daily_extend",
        func=run_daily_extend_tick,
        trigger="cron",
        trigger_args={"hour": _REVALUE_HOURS, "minute": 0},
        misfire_grace_time=_DAILY_MISFIRE_GRACE_SECONDS,
    ),
    # Refresh the casparser-isin reference DB once a day, offset from the 02:00
    # revalue. A cheap version check that downloads only when the remote is newer.
    _Job(
        id="update_isin_db",
        func=run_isin_update_tick,
        trigger="cron",
        trigger_args={"hour": 2, "minute": 30},
        misfire_grace_time=_DAILY_MISFIRE_GRACE_SECONDS,
    ),
)


def _add_jobs(scheduler):
    for job in _JOBS:
        extra = (
            {"misfire_grace_time": job.misfire_grace_time}
            if job.misfire_grace_time is not None
            else {}
        )
        scheduler.add_job(
            job.func,
            job.trigger,
            id=job.id,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
            **extra,
            **job.trigger_args,
        )
    return scheduler


def _add_catch_up_job(scheduler) -> None:
    """Register the launch catch-up as a one-shot job (no trigger → run once, now).

    It must run **on the scheduler thread** right after start, never inline on the
    caller: ``start_background_scheduler`` is invoked from ``AppConfig.ready`` (desktop),
    where a DB query trips Django's "queries during app initialization" warning and can
    grab a SQLite write lock against the still-starting process. ``misfire_grace_time
    =None`` so the brief gap before the scheduler picks the job up never skips it."""
    scheduler.add_job(
        run_catch_up_tick,
        id="catch_up_on_launch",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        misfire_grace_time=None,
    )
    # ISIN-DB launch catch-up (its daily run is skipped if the app was shut at 02:30).
    scheduler.add_job(
        run_isin_catch_up_tick,
        id="isin_catch_up_on_launch",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        misfire_grace_time=None,
    )


_background: BackgroundScheduler | None = None


def start_background_scheduler() -> BackgroundScheduler:
    """Start an in-process background scheduler (desktop). Idempotent."""
    global _background
    if _background is not None:
        return _background
    sched = BackgroundScheduler(timezone=str(settings.TIME_ZONE))
    _add_jobs(sched)
    _add_catch_up_job(sched)  # runs on the scheduler thread, not during AppConfig.ready
    sched.start()
    _background = sched
    logger.info("folioman background scheduler started (in-process)")
    return sched


def shutdown_background_scheduler(*, wait: bool = False) -> None:
    """Stop the in-process scheduler if one is running (desktop window close).

    Idempotent — a no-op when nothing was started. ``wait=False`` returns without
    blocking on a job that's mid-tick: window teardown shouldn't hang on a slow
    recompute, and the next launch's catch-up reconciles any half-done work."""
    global _background
    if _background is None:
        return
    _background.shutdown(wait=wait)
    _background = None
    logger.info("folioman background scheduler stopped")


def run_blocking_scheduler() -> None:
    """Run a blocking scheduler in the foreground (server `run_scheduler` process)."""
    sched = BlockingScheduler(timezone=str(settings.TIME_ZONE))
    _add_jobs(sched)
    _add_catch_up_job(sched)  # one-shot on the scheduler thread, fires once start() loops
    logger.info("folioman scheduler running (blocking)")
    sched.start()  # blocks
