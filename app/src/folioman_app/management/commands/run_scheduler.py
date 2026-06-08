"""manage.py run_scheduler — run the background valuation scheduler (blocking).

Used as the single dedicated scheduler process in server mode (alongside gunicorn;
never one per worker). Desktop runs the same jobs in-process via AppConfig.ready.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from folioman_app.scheduler import run_blocking_scheduler


class Command(BaseCommand):
    help = "Run the folioman background scheduler (day-wise valuation worker)."

    def handle(self, *args, **options) -> None:
        # Seed + point CASPARSER_ISIN_DB at the writable copy so this process's daily
        # ISIN refresh writes in place (no-op when no writable path is configured).
        from folioman_app.services.isin_db import ensure_isin_db

        ensure_isin_db()
        self.stdout.write(self.style.SUCCESS("Starting folioman valuation scheduler…"))
        try:
            run_blocking_scheduler()
        except (KeyboardInterrupt, SystemExit):
            self.stdout.write("Scheduler stopped.")
