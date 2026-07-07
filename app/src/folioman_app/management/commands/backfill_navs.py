"""manage.py backfill_navs — fill missing NAV/price history for all priceable securities."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from folioman_app.tasks.refresh_navs import fill_gaps


class Command(BaseCommand):
    help = "Fill missing NAV/price history (interior holes + tail) for priceable securities."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-pull each security's full span, ignoring which dates are already stored.",
        )

    def handle(self, *args, **options) -> None:
        summary = fill_gaps(force=options["force"])
        self.stdout.write(
            self.style.SUCCESS(
                f"NAV gap fill: {summary['points']} points across "
                f"{summary['securities']} securities, "
                f"{summary['skipped']} skipped, {summary['errors']} errors"
            )
        )
