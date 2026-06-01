"""manage.py refresh_navs — fetch latest NAV/price for all securities."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from folioman_app.tasks.refresh_navs import refresh_navs


class Command(BaseCommand):
    help = "Fetch the latest NAV/price for every priceable security into NAVHistory."

    def handle(self, *args, **options) -> None:
        summary = refresh_navs()
        self.stdout.write(
            self.style.SUCCESS(
                f"NAV refresh: {summary['updated']} updated, "
                f"{summary['skipped']} skipped, {summary['errors']} errors"
            )
        )
