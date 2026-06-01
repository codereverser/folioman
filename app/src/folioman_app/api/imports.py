"""Imports router: CAS upload (PAN-resolved) + per-investor job reads.

A CAS upload is **advisor-level** and identifies its own investor: the statement
carries the owner's PAN, so the server resolves it to an existing investor (by
keyed PAN hash) or creates one from the statement — no investor is chosen up
front. The flow is two-step:

- ``POST /api/imports/cas/preview`` parses and returns the owner identity (name +
  *masked* PAN) and whether it matches an existing investor. Persists nothing.
- ``POST /api/imports/cas`` resolves/creates the investor, then runs the import
  job under it. The eCAS destructive-change ``confirm`` gate is unchanged.

A statement with no PAN is rejected (422) — we can't attribute it to anyone, and
a partial import is never done. Job reads stay per-investor (jobs belong to the
resolved investor). Mounted: ``cas_router`` at ``/imports``, ``router`` at
``/investors``.
"""

from __future__ import annotations

import io

from django.shortcuts import get_object_or_404
from folioman_core.cas_reader import read_cas
from folioman_core.parser import CASParseError, CASPasswordError
from ninja import File, Form, Router, Status
from ninja.errors import HttpError
from ninja.files import UploadedFile

from folioman_app.api.auth import get_owned_investor, resolve_or_create_investor
from folioman_app.api.schemas import CasPreviewOut, ImportJobOut
from folioman_app.models import ImportJob, Investor
from folioman_app.models.jobs import ImportKind
from folioman_app.security.pan import mask_pan, pan_hash
from folioman_app.services.imports import run_import_job

router = Router(tags=["imports"])  # per-investor job reads + csv stub (mounted at /investors)
cas_router = Router(tags=["imports"])  # advisor-level CAS preview + import (mounted at /imports)

_NO_PAN_MSG = (
    "This statement has no PAN, so we can't tell whose it is. "
    "Import a statement that includes the holder's PAN."
)


def _parse_cas(content: bytes, password: str):
    """Parse CAS bytes, mapping parser failures to HTTP errors.

    Wrong password -> 400; unparseable / unsupported / multi-PAN -> 422 (the
    parser's message is PII-free, safe to surface)."""
    try:
        return read_cas(io.BytesIO(content), password)
    except CASPasswordError as exc:
        raise HttpError(400, "Incorrect password for this statement.") from exc
    except CASParseError as exc:
        raise HttpError(422, str(exc)) from exc


@cas_router.post("/cas/preview", response=CasPreviewOut)
def preview_cas(request, file: UploadedFile = File(...), password: str = Form("")):
    """Parse a CAS and report whose it is — without persisting anything.

    Returns the owner's name + masked PAN and, when the PAN already matches an
    investor, that investor's id/name so the UI can offer 'attach' vs 'create'.
    """
    parsed = _parse_cas(file.read(), password)
    identity = parsed.investor
    if not identity.pan:
        raise HttpError(422, _NO_PAN_MSG)
    match = Investor.objects.filter(owned_by=request.auth, pan_hash=pan_hash(identity.pan)).first()
    return CasPreviewOut(
        kind="ecas" if parsed.is_ecas else "mf_cas",
        investor_name=identity.name,
        investor_email=identity.email,
        pan_masked=mask_pan(identity.pan),
        match_investor_id=match.id if match else None,
        match_investor_name=match.name if match else None,
    )


@cas_router.post("/cas", response={201: ImportJobOut})
def import_cas(
    request,
    file: UploadedFile = File(...),
    password: str = Form(""),
    confirm: bool = Form(False),
):
    """Import a CAS, resolving (or creating) its investor by PAN.

    Auto-detects MF CAS vs NSDL/CDSL eCAS. An eCAS that would *remove* securities
    returns the job at status ``needs_confirmation`` with ``result.removals`` and
    persists nothing — resubmit with ``confirm=true`` to apply. A PAN-less
    statement is rejected (422); nothing is created.
    """
    content = file.read()
    # Parse once to resolve the investor; the job processor re-parses to persist.
    parsed = _parse_cas(content, password)
    if not parsed.investor.pan:
        raise HttpError(422, _NO_PAN_MSG)
    investor, _created = resolve_or_create_investor(request.auth, parsed.investor)
    job = ImportJob.objects.create(investor=investor, kind=ImportKind.CAS, filename=file.name or "")
    run_import_job(job, content=content, password=password, confirm=confirm)
    return Status(201, job)


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
