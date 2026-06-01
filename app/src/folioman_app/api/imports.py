"""Imports router: per-investor file uploads via the synchronous job flow.

POST an MF CAS / eCAS / CSV file (multipart + optional password); the server
creates an ImportJob, runs it synchronously, and returns the job. The client
reads / polls `GET /api/investors/{id}/imports/{job_id}`. Mounted under
`/investors`, so paths are `/api/investors/{investor_id}/imports/...`.

The CAS / eCAS / CSV parsers register their processors; an unregistered kind
records a clear failure on the job.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from ninja import File, Form, Router, Status
from ninja.errors import HttpError
from ninja.files import UploadedFile

from folioman_app.api.auth import get_owned_investor
from folioman_app.api.schemas import ImportJobOut
from folioman_app.models import ImportJob
from folioman_app.models.jobs import ImportKind
from folioman_app.services.imports import run_import_job

router = Router(tags=["imports"])


def _run_upload(
    request, investor_id: int, kind: str, file: UploadedFile, password: str, confirm: bool = False
):
    investor = get_owned_investor(request, investor_id)
    job = ImportJob.objects.create(investor=investor, kind=kind, filename=file.name or "")
    run_import_job(job, content=file.read(), password=password, confirm=confirm)
    return Status(201, job)


@router.post("/{investor_id}/imports/cas", response={201: ImportJobOut})
def import_cas(
    request,
    investor_id: int,
    file: UploadedFile = File(...),
    password: str = Form(""),
    confirm: bool = Form(False),
):
    """Import any CAS PDF — a CAMS/KFin MF CAS or an NSDL/CDSL eCAS.

    The type is auto-detected: an MF CAS becomes a transaction ledger; an eCAS is
    the depository's authoritative snapshot (refreshes holdings). If applying an
    eCAS would *remove* securities, the job returns status ``needs_confirmation``
    with ``result.removals`` and persists nothing — resubmit with ``confirm=true``
    to apply. An eCAS older than the latest on file is rejected.
    """
    return _run_upload(request, investor_id, ImportKind.CAS, file, password, confirm)


@router.post("/{investor_id}/imports/csv", response={201: ImportJobOut})
def import_csv(request, investor_id: int, file: UploadedFile = File(...)):
    # Disabled until the multi-asset phase: imports are security-specific now
    # (mutual funds via CAS PDF; equities via eCAS / per-broker templated CSV).
    # Endpoint kept so the contract is stable; re-enable by restoring the call.
    raise HttpError(503, "CSV import is disabled until the multi-asset phase.")


@router.get("/{investor_id}/imports", response=list[ImportJobOut])
def list_import_jobs(request, investor_id: int):
    investor = get_owned_investor(request, investor_id)
    return list(investor.import_jobs.all())


@router.get("/{investor_id}/imports/{job_id}", response=ImportJobOut)
def get_import_job(request, investor_id: int, job_id: int):
    investor = get_owned_investor(request, investor_id)
    # Scope the job to the investor — a job belonging to another investor 404s.
    return get_object_or_404(ImportJob, id=job_id, investor=investor)
