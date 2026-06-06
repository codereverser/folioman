"""manage.py backfill_navs — backfill full NAV history (mfapi) for MF securities."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from folioman_app.tasks.refresh_navs import backfill_missing_history


class Command(BaseCommand):
    help = "Backfill full NAV history (mfapi) for MF securities into NAVHistory."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Re-pull every fund's full history, ignoring the freshness skip — "
                "repairs interior gaps where the latest point is current but earlier "
                "trading days are missing."
            ),
        )

    def handle(self, *args, **options) -> None:
        summary = backfill_missing_history(force=options["force"])
        self.stdout.write(
            self.style.SUCCESS(
                f"NAV backfill: {summary['points']} points across "
                f"{summary['securities']} securities, "
                f"{summary['skipped']} skipped, {summary['errors']} errors"
            )
        )
