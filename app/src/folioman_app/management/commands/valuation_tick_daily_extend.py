"""manage.py valuation_tick_daily_extend — run one daily-extend pass and exit.

The one-shot, broker-free entrypoint an external scheduler (OS cron, systemd
timer, Kubernetes CronJob, a Celery Beat shell task) invokes to replace the
in-process/`run_scheduler` clock at scale. Calls the same scheduler-neutral tick
the APScheduler daily cron job uses. Run exactly one trigger source per environment.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from folioman_app.tasks.valuation_ticks import run_daily_extend_tick


class Command(BaseCommand):
    help = "Roll ready day-wise valuation series forward and re-queue errors (one pass)."

    def handle(self, *args, **options) -> None:
        queued = run_daily_extend_tick()
        self.stdout.write(self.style.SUCCESS(f"Daily valuation extend: {queued} queued"))
