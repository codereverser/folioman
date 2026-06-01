"""ImportJob: a synchronous import status row (no task queue in v1).

Imports run synchronously (see Key decisions): an import creates an ImportJob,
runs the parse + upsert in-process, then updates the row to SUCCESS/FAILED with
a result summary. The API returns the job id and the client polls
``GET /api/investors/{id}/imports/{job_id}``. Investor-scoped so the flow knows
exactly what is being mutated.
"""

from __future__ import annotations

from django.db import models

from folioman_app.models.base import TimeStampedModel
from folioman_app.models.ledger import Investor


class ImportKind(models.TextChoices):
    # Single auto-detecting CAS upload (CAMS/KFin MF CAS or NSDL/CDSL eCAS); the
    # processor records which it actually was in result["detected"].
    CAS = "cas", "CAS PDF"
    CSV = "csv", "CSV"
    MANUAL = "manual", "Manual entry"


class ImportJobStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    # Import data committed, but post-commit reconcile failed for one or more
    # securities (details in ``result['reconcile_errors']``). The data is safe;
    # reconcile needs a retry. Distinct from FAILED, where nothing was persisted.
    COMPLETED_WITH_WARNINGS = "completed_with_warnings", "Completed with warnings"
    # A destructive eCAS import (would remove securities) was previewed but NOT
    # applied — awaiting user confirmation. Nothing was persisted; the removals
    # are in ``result['removals']``. Re-submit with confirm=true to apply.
    NEEDS_CONFIRMATION = "needs_confirmation", "Needs confirmation"
    FAILED = "failed", "Failed"


class ImportJob(TimeStampedModel):
    investor = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name="import_jobs")
    kind = models.CharField(max_length=16, choices=ImportKind.choices)
    status = models.CharField(
        max_length=24, choices=ImportJobStatus.choices, default=ImportJobStatus.PENDING
    )
    filename = models.CharField(max_length=255, blank=True, default="")
    source_ref = models.CharField(max_length=128, blank=True, default="")  # file hash
    # Summary counts (securities/transactions/holdings created, skipped, etc.).
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["investor", "status"], name="idx_importjob_inv_status"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} [{self.status}] for investor {self.investor_id}"
