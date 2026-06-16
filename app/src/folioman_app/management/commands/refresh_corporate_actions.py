"""manage.py refresh_corporate_actions — cache NSE/BSE corporate actions."""

from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from folioman_app.tasks.refresh_corporate_actions import refresh_corporate_actions


class Command(BaseCommand):
    help = "Fetch corporate actions for equity securities into CorporateActionReference."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--symbol",
            metavar="SYM",
            help="Refresh one equity by trading symbol (default: all equities).",
        )
        parser.add_argument(
            "--since",
            metavar="YYYY-MM-DD",
            help="Earliest ex-date to fetch (default: 2016-01-01).",
        )

    def handle(self, *args, **options) -> None:
        since = None
        if options["since"]:
            try:
                since = date.fromisoformat(options["since"])
            except ValueError as exc:
                raise CommandError(f"invalid --since date: {options['since']!r}") from exc
        summary = refresh_corporate_actions(symbol=options["symbol"], since=since)
        self.stdout.write(
            self.style.SUCCESS(
                f"Corporate actions: {summary['securities']} securities, "
                f"{summary['events']} events, {summary['skipped']} skipped, "
                f"{summary['errors']} errors"
            )
        )
