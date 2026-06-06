"""The in-process scheduler autostart gate in ``FoliomanAppConfig``.

``runserver`` autoreload runs ``AppConfig.ready()`` in BOTH the reloader parent and
the worker child, so the scheduler must start in exactly one of them — otherwise two
scheduler threads in two processes fight over the SQLite file. The gate starts it
only in the ``RUN_MAIN=true`` child, but never gates the ``--noreload`` / desktop /
other-entrypoint cases (which have no child and never set ``RUN_MAIN``).
"""

from __future__ import annotations

import sys

import pytest
from django.apps import apps


@pytest.fixture
def cfg():
    return apps.get_app_config("folioman_app")


@pytest.fixture
def starts(monkeypatch):
    """Count scheduler starts without launching a real one."""
    import folioman_app.scheduler as scheduler

    calls = {"n": 0}
    monkeypatch.setattr(
        scheduler, "start_background_scheduler", lambda: calls.__setitem__("n", calls["n"] + 1)
    )
    return calls


def _arrange(monkeypatch, settings, *, argv, run_main=None, flag=True):
    settings.FOLIOMAN_RUN_SCHEDULER = flag
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delenv("FOLIOMAN_DEFER_SCHEDULER", raising=False)
    if run_main is None:
        monkeypatch.delenv("RUN_MAIN", raising=False)
    else:
        monkeypatch.setenv("RUN_MAIN", run_main)


def test_runserver_reloader_parent_does_not_start(monkeypatch, settings, cfg, starts):
    # Parent process under autoreload: RUN_MAIN unset → the child starts it, not us.
    _arrange(monkeypatch, settings, argv=["manage.py", "runserver"], run_main=None)
    cfg._maybe_start_scheduler()
    assert starts["n"] == 0


def test_runserver_worker_child_starts(monkeypatch, settings, cfg, starts):
    _arrange(monkeypatch, settings, argv=["manage.py", "runserver"], run_main="true")
    cfg._maybe_start_scheduler()
    assert starts["n"] == 1


def test_runserver_noreload_starts(monkeypatch, settings, cfg, starts):
    # No child process exists with --noreload, so RUN_MAIN is never set — start here.
    _arrange(monkeypatch, settings, argv=["manage.py", "runserver", "--noreload"], run_main=None)
    cfg._maybe_start_scheduler()
    assert starts["n"] == 1


def test_non_runserver_entrypoint_starts(monkeypatch, settings, cfg, starts):
    # Desktop / pywebview launch: not runserver, no RUN_MAIN — must start.
    _arrange(monkeypatch, settings, argv=["folioman-desktop"], run_main=None)
    cfg._maybe_start_scheduler()
    assert starts["n"] == 1


def test_excluded_command_does_not_start(monkeypatch, settings, cfg, starts):
    _arrange(monkeypatch, settings, argv=["manage.py", "migrate"], run_main="true")
    cfg._maybe_start_scheduler()
    assert starts["n"] == 0


def test_flag_off_does_not_start(monkeypatch, settings, cfg, starts):
    _arrange(monkeypatch, settings, argv=["folioman-desktop"], run_main=None, flag=False)
    cfg._maybe_start_scheduler()
    assert starts["n"] == 0


def test_defer_flag_holds_off_autostart(monkeypatch, settings, cfg, starts):
    # The desktop launcher sets this before django.setup, then starts the scheduler
    # itself after migrate — so ready() must NOT start it during bootstrap.
    _arrange(monkeypatch, settings, argv=["folioman-desktop"], run_main=None)
    monkeypatch.setenv("FOLIOMAN_DEFER_SCHEDULER", "1")
    cfg._maybe_start_scheduler()
    assert starts["n"] == 0


def test_start_then_shutdown_lifecycle(monkeypatch):
    # The desktop launcher owns start (after migrate) and shutdown (window close).
    from folioman_app import scheduler

    # Stub the one-shot launch catch-up: its DB tick (covered in test_valuation_ticks)
    # would otherwise race this start→shutdown on the scheduler thread. We only assert
    # the start/shutdown lifecycle here, not the catch-up.
    monkeypatch.setattr(scheduler, "_add_catch_up_job", lambda sched: None)

    sched = scheduler.start_background_scheduler()
    try:
        assert sched.running
        # Idempotent start: a second call returns the same instance, not a new one.
        assert scheduler.start_background_scheduler() is sched
    finally:
        scheduler.shutdown_background_scheduler()
    assert not sched.running
    scheduler.shutdown_background_scheduler()  # second teardown is a no-op


def test_shutdown_with_nothing_running_is_a_noop():
    from folioman_app import scheduler

    scheduler.shutdown_background_scheduler()  # never started → must not raise
