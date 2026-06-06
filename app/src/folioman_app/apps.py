"""App configuration for the Folioman Django application."""

from __future__ import annotations

import os
import sys

from django.apps import AppConfig
from django.conf import settings

# Management commands during which the in-process scheduler must NOT auto-start
# (migrations, tests, schema/asset tooling, or the dedicated scheduler process).
_NO_SCHEDULER_COMMANDS = {
    "migrate",
    "makemigrations",
    "collectstatic",
    "test",
    "shell",
    "export_openapi",
    "run_scheduler",
    "valuation_tick_pending",
    "valuation_tick_daily_extend",
    "refresh_navs",
    "backfill_navs",
}


class FoliomanAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "folioman_app"
    verbose_name = "Folioman"

    def ready(self) -> None:
        # The app is a thin layer over the framework-free domain library.
        # Fail fast at startup if the workspace dependency isn't wired in.
        import folioman_core  # noqa: F401

        # Import the tasks package so import processors register with the runner.
        from folioman_app import tasks  # noqa: F401

        # Register the PAN-encryption-key startup guard (system check).
        from folioman_app.security import checks  # noqa: F401

        self._maybe_start_scheduler()

    def _maybe_start_scheduler(self) -> None:
        """Desktop runs the valuation scheduler in-process (a thread) when
        FOLIOMAN_RUN_SCHEDULER is on; server uses a dedicated `run_scheduler`
        process instead (flag off). Skip during migrations/tests/tooling."""
        if not getattr(settings, "FOLIOMAN_RUN_SCHEDULER", False):
            return
        # The desktop launcher defers the scheduler until after first-run migrate,
        # then starts it explicitly so it also owns the shutdown on window close.
        # Without this, ready() (run during django.setup) would start the catch-up
        # tick against a not-yet-migrated DB on a fresh install.
        if os.environ.get("FOLIOMAN_DEFER_SCHEDULER") == "1":
            return
        argv1 = sys.argv[1] if len(sys.argv) > 1 else ""
        if argv1 in _NO_SCHEDULER_COMMANDS:
            return
        # `runserver` autoreload runs ready() in BOTH the reloader parent and the
        # worker child — start the scheduler only in the child (RUN_MAIN=true), else
        # two scheduler threads in two processes fight over the SQLite file. The
        # `--noreload` case has no child, so don't gate on RUN_MAIN there; desktop /
        # other entrypoints never set RUN_MAIN and start normally.
        uses_autoreload = argv1 == "runserver" and "--noreload" not in sys.argv
        if uses_autoreload and os.environ.get("RUN_MAIN") != "true":
            return
        from folioman_app.scheduler import start_background_scheduler

        start_background_scheduler()
