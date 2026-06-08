"""manage.py update_isin_db — check for and apply a newer casparser-isin DB, then exit.

The one-shot, broker-free entrypoint an external scheduler (OS cron, systemd timer,
Kubernetes CronJob) invokes to replace the in-process / ``run_scheduler`` clock for
the ISIN refresh. Calls the same scheduler-neutral tick the APScheduler daily job
uses. Run exactly one trigger source per environment.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from folioman_app.tasks.isin_ticks import run_isin_update_tick


class Command(BaseCommand):
    help = "Refresh the casparser-isin database if a newer version is available (one pass)."

    def handle(self, *args, **options) -> None:
        ran = run_isin_update_tick()
        if ran:
            self.stdout.write(self.style.SUCCESS("casparser-isin update check complete"))
        else:
            self.stdout.write("casparser-isin update skipped (no writable DB path configured)")
