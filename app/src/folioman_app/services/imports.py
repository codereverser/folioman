"""Synchronous import-job runner (no task queue — see Key decisions).

An import creates an ``ImportJob``, then ``run_import_job`` dispatches by kind to
a registered processor, recording SUCCESS + a result summary or FAILED + the
error on the job row. The raw uploaded file is processed in memory and never
persisted (privacy-first); only its SHA-256 ``source_ref`` is kept, for dedup.

Processors are registered by the per-kind import services: CAS PDF,
eCAS, CSV. Until a kind is registered, its job fails with a clear
"not implemented yet" message — the flow + API are fully wired now.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from django.utils import timezone

from folioman_app.models import ImportJob
from folioman_app.models.jobs import ImportJobStatus

# processor(job, content, password, *, confirm, parsed) -> result summary dict
ImportProcessor = Callable[..., dict]
_PROCESSORS: dict[str, ImportProcessor] = {}


def register_processor(kind: str, processor: ImportProcessor) -> None:
    _PROCESSORS[kind] = processor


def run_import_job(
    job: ImportJob,
    *,
    content: bytes,
    password: str = "",
    confirm: bool = False,
    parsed: object | None = None,
) -> ImportJob:
    """Run an import synchronously, recording the outcome on ``job``.

    ``confirm`` opts into a destructive import (an eCAS that removes securities);
    without it such an import is previewed but not applied (NEEDS_CONFIRMATION).
    ``parsed`` hands the processor an already-parsed statement so it needn't
    re-parse the PDF (the upload path parses once to resolve the investor).
    """
    job.source_ref = hashlib.sha256(content).hexdigest()
    job.started_at = timezone.now()
    processor = _PROCESSORS.get(job.kind)
    try:
        if processor is None:
            msg = f"{job.kind} importer not implemented yet"
            raise NotImplementedError(msg)
        job.result = processor(job, content, password, confirm=confirm, parsed=parsed) or {}
        if job.result.get("requires_confirmation"):
            # Previewed a destructive import; persisted nothing. Await confirm.
            job.status = ImportJobStatus.NEEDS_CONFIRMATION
        elif job.result.get("reconcile_errors") or job.result.get("incomplete_history"):
            # Warnings leave real data behind but need the user's attention —
            # COMPLETED_WITH_WARNINGS, not FAILED (which implies nothing imported):
            # a post-commit reconcile failure, or incomplete-history snapshots.
            job.status = ImportJobStatus.COMPLETED_WITH_WARNINGS
        else:
            job.status = ImportJobStatus.SUCCESS
        job.error = ""
    except Exception as exc:  # any failure is recorded on the job, not raised
        job.status = ImportJobStatus.FAILED
        job.error = str(exc)
    job.finished_at = timezone.now()
    job.save()
    return job
