"""App configuration for the Folioman Django application."""

from __future__ import annotations

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
        argv1 = sys.argv[1] if len(sys.argv) > 1 else ""
        if argv1 in _NO_SCHEDULER_COMMANDS:
            return
        from folioman_app.scheduler import start_background_scheduler

        start_background_scheduler()
