"""manage.py valuation_tick_pending — run one pending-valuation pass and exit.

The one-shot, broker-free entrypoint an external scheduler (OS cron, systemd
timer, Kubernetes CronJob, a Celery Beat shell task) invokes to replace the
in-process/`run_scheduler` clock at scale. Calls the same scheduler-neutral tick
the APScheduler interval job uses. Run exactly one trigger source per environment.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from folioman_app.tasks.valuation_ticks import run_pending_valuations_tick


class Command(BaseCommand):
    help = "Process investors with pending/retryable day-wise valuation (one pass)."

    def handle(self, *args, **options) -> None:
        processed = run_pending_valuations_tick()
        self.stdout.write(self.style.SUCCESS(f"Pending valuation pass: {processed} processed"))
