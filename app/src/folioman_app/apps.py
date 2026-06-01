"""App configuration for the Folioman Django application."""

from __future__ import annotations

from django.apps import AppConfig


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
