"""manage.py backfill_navs — backfill full NAV history (mfapi) for MF securities."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from folioman_app.tasks.refresh_navs import backfill_missing_history


class Command(BaseCommand):
    help = "Backfill full NAV history (mfapi) for MF securities into NAVHistory."

    def handle(self, *args, **options) -> None:
        summary = backfill_missing_history()
        self.stdout.write(
            self.style.SUCCESS(
                f"NAV backfill: {summary['points']} points across "
                f"{summary['securities']} securities, {summary['errors']} errors"
            )
        )
