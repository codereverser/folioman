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


def _preview_stats(parsed) -> dict:
    """Content stats for the preview, so the UI can show what's inside and flag a
    Summary/partial CAS (net-worth only) before importing."""
    if parsed.is_ecas:
        ecas = parsed.ecas
        return {
            "from_date": None,
            "to_date": ecas.statement_date,
            "scheme_count": len(ecas.accounts),
            "transaction_count": 0,
            "holding_count": sum(len(a.holdings) for a in ecas.accounts),
            "full_history": False,  # a depository snapshot, never a cost-basis ledger
            "snapshot_scheme_count": 0,
            "skipped_unidentified": 0,
        }
    mf = parsed.mf
    transactions = sum(len(s.transactions) for s in mf.schemes)
    # A scheme lands as net-worth-only if it has no transactions (a Summary CAS) or
    # carries a non-zero opening with no earlier history (a partial-period CAS).
    snapshot = sum(
        1
        for s in mf.schemes
        if not s.transactions or not (s.opening_units is None or s.opening_units == 0)
    )
    return {
        "from_date": mf.statement_from,
        "to_date": mf.statement_to,
        "scheme_count": len(mf.schemes),
        "transaction_count": transactions,
        "holding_count": 0,
        "full_history": transactions > 0 and snapshot == 0,
        "snapshot_scheme_count": snapshot,
        "skipped_unidentified": mf.skipped_unidentified,
    }


@cas_router.post("/cas/preview", response=CasPreviewOut)
def preview_cas(request, file: UploadedFile = File(...), password: str = Form("")):
    """Parse a CAS and report whose it is + what's inside — persisting nothing.

    Returns the owner's name + masked PAN (and an existing-investor match for
    'attach' vs 'create'), plus content stats (period, counts, full-history vs
    snapshot) so the UI can flag a Summary/partial statement before import.
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
        **_preview_stats(parsed),
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
    # Parse once to resolve the investor, then hand that parse to the job processor
    # so the PDF isn't parsed again to persist.
    parsed = _parse_cas(content, password)
    if not parsed.investor.pan:
        raise HttpError(422, _NO_PAN_MSG)
    investor, _created = resolve_or_create_investor(request.auth, parsed.investor)
    job = ImportJob.objects.create(investor=investor, kind=ImportKind.CAS, filename=file.name or "")
    run_import_job(job, content=content, password=password, confirm=confirm, parsed=parsed)
    return Status(201, job)


@router.post("/{investor_id}/imports/csv", response={201: ImportJobOut})
def import_csv(request, investor_id: int, file: UploadedFile = File(...)):
    # Disabled in the first release: imports are security-specific now (mutual
    # funds via CAS PDF; equities via eCAS / per-broker templated CSV, which ship
    # with multi-asset support). Endpoint kept so the contract is stable;
    # re-enable by restoring the call.
    raise HttpError(503, "CSV import isn't available yet — import a CAS instead.")


@router.get("/{investor_id}/imports", response=list[ImportJobOut])
def list_import_jobs(request, investor_id: int):
    investor = get_owned_investor(request, investor_id)
    return list(investor.import_jobs.all())


@router.get("/{investor_id}/imports/{job_id}", response=ImportJobOut)
def get_import_job(request, investor_id: int, job_id: int):
    investor = get_owned_investor(request, investor_id)
    # Scope the job to the investor — a job belonging to another investor 404s.
    return get_object_or_404(ImportJob, id=job_id, investor=investor)
