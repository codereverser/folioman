"""Scheduler-neutral valuation ticks + the trigger seam.

The ticks are the stable contract every trigger (APScheduler, one-shot
management commands, future external schedulers) calls. These tests pin that
seam: ticks delegate to the job functions, contain exceptions, the management
commands drive the tick layer, the APScheduler registry points at the ticks, and
no scheduler/broker import leaks into the scheduler-neutral modules.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from folioman_app import scheduler
from folioman_app.tasks import valuation_jobs, valuation_ticks

pytestmark = pytest.mark.django_db


def test_pending_tick_delegates_to_job(monkeypatch):
    monkeypatch.setattr(valuation_jobs, "process_pending_valuations", lambda: 7)
    assert valuation_ticks.run_pending_valuations_tick() == 7


def test_daily_extend_tick_delegates_to_job(monkeypatch):
    monkeypatch.setattr(valuation_jobs, "enqueue_daily_extend", lambda: 3)
    assert valuation_ticks.run_daily_extend_tick() == 3


def test_tick_contains_job_exception(monkeypatch):
    def boom():
        raise RuntimeError("feed down")

    monkeypatch.setattr(valuation_jobs, "process_pending_valuations", boom)
    # A recurring trigger must keep ticking: the exception is swallowed -> 0.
    assert valuation_ticks.run_pending_valuations_tick() == 0


def test_pending_command_drives_tick(monkeypatch):
    called = {"n": 0}

    def fake_tick() -> int:
        called["n"] += 1
        return 5

    monkeypatch.setattr(
        "folioman_app.management.commands.valuation_tick_pending.run_pending_valuations_tick",
        fake_tick,
    )
    out = StringIO()
    call_command("valuation_tick_pending", stdout=out)
    assert called["n"] == 1
    assert "5 processed" in out.getvalue()


def test_daily_extend_command_drives_tick(monkeypatch):
    called = {"n": 0}

    def fake_tick() -> int:
        called["n"] += 1
        return 2

    monkeypatch.setattr(
        "folioman_app.management.commands.valuation_tick_daily_extend.run_daily_extend_tick",
        fake_tick,
    )
    out = StringIO()
    call_command("valuation_tick_daily_extend", stdout=out)
    assert called["n"] == 1
    assert "2 queued" in out.getvalue()


def test_scheduler_registry_points_at_ticks():
    by_id = {job.id: job for job in scheduler._JOBS}
    assert by_id["process_pending_valuations"].func is valuation_ticks.run_pending_valuations_tick
    assert by_id["process_pending_valuations"].trigger == "interval"
    assert by_id["enqueue_daily_extend"].func is valuation_ticks.run_daily_extend_tick
    assert by_id["enqueue_daily_extend"].trigger == "cron"


def test_daily_cron_has_misfire_grace():
    # A process asleep across 02:00 must still fire the missed daily extend on wake;
    # the interval tick keeps APScheduler's tight default (no stale catch-up wanted).
    by_id = {job.id: job for job in scheduler._JOBS}
    daily = by_id["enqueue_daily_extend"]
    assert daily.misfire_grace_time == scheduler._DAILY_MISFIRE_GRACE_SECONDS
    assert daily.misfire_grace_time > 0
    assert by_id["process_pending_valuations"].misfire_grace_time is None


def test_add_jobs_applies_misfire_grace_and_coalesce():
    recorded = []

    class _FakeSched:
        def add_job(self, func, trigger, **kw):
            recorded.append(kw)

    scheduler._add_jobs(_FakeSched())
    by_id = {kw["id"]: kw for kw in recorded}
    assert all(kw["coalesce"] and kw["max_instances"] == 1 for kw in recorded)
    grace = by_id["enqueue_daily_extend"]["misfire_grace_time"]
    assert grace == scheduler._DAILY_MISFIRE_GRACE_SECONDS
    # The interval tick keeps APScheduler's default — no misfire_grace_time passed.
    assert "misfire_grace_time" not in by_id["process_pending_valuations"]


def test_background_start_schedules_catch_up_off_the_init_thread(monkeypatch):
    """Catch-up must be a one-shot job on the scheduler thread — never invoked inline
    during AppConfig.ready, where its DB query warns and can lock SQLite at startup."""
    added = []
    invoked = {"n": 0}

    class _FakeSched:
        def add_job(self, func, *a, **k):
            added.append((func, k.get("id")))

        def start(self):
            pass

    monkeypatch.setattr(scheduler, "_background", None)
    monkeypatch.setattr(scheduler, "BackgroundScheduler", lambda **kw: _FakeSched())
    monkeypatch.setattr(scheduler, "run_catch_up_tick", lambda: invoked.__setitem__("n", 1) or 0)

    scheduler.start_background_scheduler()

    catch_up = [(f, jid) for f, jid in added if jid == "catch_up_on_launch"]
    assert len(catch_up) == 1  # scheduled once as a one-shot job
    assert catch_up[0][0] is scheduler.run_catch_up_tick
    assert invoked["n"] == 0  # not called inline on the init thread


def test_no_scheduler_import_leaks_into_neutral_modules():
    # The job + tick modules must stay broker/clock-free so they run from a bare
    # manage.py process and inside the pure-Python desktop build.
    for module in (valuation_jobs, valuation_ticks):
        src = Path(module.__file__).read_text()
        assert "import apscheduler" not in src
        assert "from apscheduler" not in src
