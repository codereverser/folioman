"""manage.py reconcile_all — re-reconcile every security for every investor."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from folioman_app.tasks.reconcile import reconcile_all_investors


class Command(BaseCommand):
    help = "Re-reconcile every security for every investor (refreshes integrity)."

    def handle(self, *args, **options) -> None:
        summary = reconcile_all_investors()
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {summary['statuses']} statuses across {summary['investors']} investors"
            )
        )
