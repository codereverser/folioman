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

from folioman_app.models import Holding, ImportJob, ImportQuarantine, Transaction
from folioman_app.models.jobs import ImportJobStatus

# processor(job, content, password, *, confirm, parsed) -> result summary dict
ImportProcessor = Callable[..., dict]
_PROCESSORS: dict[str, ImportProcessor] = {}


def register_processor(kind: str, processor: ImportProcessor) -> None:
    _PROCESSORS[kind] = processor


def resolve_quarantine(investor) -> int:
    """Auto-resolve open quarantine rows whose (security, folio) now has data.

    After a corrected statement is re-imported, a previously-rejected security/folio
    that now persists cleanly no longer needs attention. Matches on ISIN + folio
    number; a row with no ISIN can't be matched here and stays until dismissed.
    Returns the number resolved."""
    open_rows = (
        ImportQuarantine.objects.filter(investor=investor, resolved=False)
        # A folio-link suggestion is resolved by the user picking a target, not by
        # an (isin, folio) match — exclude it from the data-presence auto-resolver.
        .exclude(kind="folio_link")
        .exclude(isin="")
        .values("id", "isin", "folio_number")
    )
    fixed = [
        q["id"]
        for q in open_rows
        if Transaction.objects.filter(
            investor=investor, security__isin=q["isin"], folio__number=q["folio_number"]
        ).exists()
        or Holding.objects.filter(
            investor=investor, security__isin=q["isin"], folio__number=q["folio_number"]
        ).exists()
    ]
    if fixed:
        ImportQuarantine.objects.filter(id__in=fixed).update(
            resolved=True, resolved_at=timezone.now()
        )
    return len(fixed)


def _record_quarantine(job: ImportJob) -> None:
    """Persist the rows a processor set aside (``result["quarantined"]``)."""
    entries = job.result.get("quarantined") or []
    if not entries:
        return
    kind = job.result.get("detected", "")
    ImportQuarantine.objects.bulk_create(
        [
            ImportQuarantine(
                investor=job.investor,
                import_job=job,
                # An entry may override the job-level kind (e.g. a "folio_link"
                # suggestion raised mid-eCAS, distinct from the eCAS's own rejects).
                kind=entry.get("kind") or kind,
                security_name=entry.get("security", ""),
                isin=entry.get("isin", ""),
                folio_number=entry.get("folio", ""),
                reason=entry.get("reason", ""),
                raw=entry.get("raw", {}),
            )
            for entry in entries
        ]
    )


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
        elif (
            job.result.get("reconcile_errors")
            or job.result.get("incomplete_history")
            or job.result.get("quarantined")
        ):
            # Warnings leave real data behind but need the user's attention —
            # COMPLETED_WITH_WARNINGS, not FAILED (which implies nothing imported):
            # a post-commit reconcile failure, incomplete-history snapshots, or rows
            # set aside in quarantine.
            job.status = ImportJobStatus.COMPLETED_WITH_WARNINGS
        else:
            job.status = ImportJobStatus.SUCCESS
        job.error = ""
    except Exception as exc:  # any failure is recorded on the job, not raised
        job.status = ImportJobStatus.FAILED
        job.error = str(exc)
    job.finished_at = timezone.now()
    job.save()
    # A run that persisted data may have fixed earlier quarantined rows (a corrected
    # re-import) — clear those first, then record this run's new rejects. A pure
    # failure or an unconfirmed destructive preview persisted nothing, so skip both.
    if job.status in (ImportJobStatus.SUCCESS, ImportJobStatus.COMPLETED_WITH_WARNINGS):
        resolve_quarantine(job.investor)
        _record_quarantine(job)
    return job
