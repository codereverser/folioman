"""Integrity router (per investor) — the trust differentiator surfaced as API.

List the per-(security, folio) reconciliation statuses, force a full recompute,
or acknowledge a specific mismatch (record that the user accepts the gap; it
stays out of the tax export).
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError

from folioman_app.api.auth import get_owned_investor
from folioman_app.api.schemas import IntegrityStatusOut
from folioman_app.models import Folio, Investor, Security, SecurityIntegrityStatus
from folioman_app.tasks.reconcile import recompute_investor, reconcile_security_folio

router = Router(tags=["integrity"])


def _statuses(investor: Investor):
    return list(investor.integrity_statuses.select_related("security", "folio").all())


@router.get("/{investor_id}/integrity", response=list[IntegrityStatusOut])
def list_integrity(request, investor_id: int):
    return _statuses(get_owned_investor(request, investor_id))


@router.post("/{investor_id}/integrity/recompute", response=list[IntegrityStatusOut])
def recompute(request, investor_id: int):
    investor = get_owned_investor(request, investor_id)
    recompute_investor(investor)
    return _statuses(investor)


def _owned_status(investor, security_id: int, folio_id: int):
    """Resolve (security, folio) for an existing integrity row, or 404."""
    security = get_object_or_404(Security, id=security_id)
    folio = get_object_or_404(Folio, id=folio_id, investor=investor)
    if not SecurityIntegrityStatus.objects.filter(
        investor=investor, security=security, folio=folio
    ).exists():
        raise HttpError(404, "no integrity status for this security/folio")
    return security, folio


@router.post(
    "/{investor_id}/integrity/{security_id}/{folio_id}/acknowledge",
    response=IntegrityStatusOut,
)
def acknowledge(request, investor_id: int, security_id: int, folio_id: int):
    investor = get_owned_investor(request, investor_id)
    security, folio = _owned_status(investor, security_id, folio_id)
    return reconcile_security_folio(investor, security, folio, acknowledge=True)


@router.post(
    "/{investor_id}/integrity/{security_id}/{folio_id}/unacknowledge",
    response=IntegrityStatusOut,
)
def unacknowledge(request, investor_id: int, security_id: int, folio_id: int):
    """Undo an acknowledgement: the row reverts to its real status (an unresolved
    gap reappears as a mismatch). Lets the user take back a mis-click."""
    investor = get_owned_investor(request, investor_id)
    security, folio = _owned_status(investor, security_id, folio_id)
    return reconcile_security_folio(investor, security, folio, clear_acknowledgement=True)
