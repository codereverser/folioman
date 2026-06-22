"""Integrity router (per investor) — the trust differentiator surfaced as API.

List the per-(security, folio) reconciliation statuses, force a full recompute,
or acknowledge a specific mismatch (record that the user accepts the gap; it
stays out of the tax export).
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from folioman_core.models import SecurityType
from folioman_core.opening_lot import OpeningLotKind
from folioman_core.reconciliation import IntegrityStatus
from ninja import Router
from ninja.errors import HttpError

from folioman_app.api.auth import get_owned_investor
from folioman_app.api.schemas import (
    ApplyCorporateActionIn,
    ApplyCorporateActionOut,
    IdentityRemapIn,
    IdentityRemapOut,
    IntegrityStatusOut,
    ManualCorporateActionIn,
    RecordOpeningLotIn,
    RecordOpeningLotOut,
    RecordOpeningLotsIn,
    RecordOpeningLotsOut,
)
from folioman_app.models import Folio, Investor, Security, SecurityIntegrityStatus
from folioman_app.services.corporate_actions import (
    apply_manual_corporate_action,
    apply_suggested_corporate_actions,
)
from folioman_app.services.identity_remap import apply_identity_remap
from folioman_app.services.opening_lots import record_opening_lot, record_opening_lots
from folioman_app.tasks.reconcile import (
    recompute_investor,
    reconcile_security,
    reconcile_security_folio,
)
from folioman_app.tasks.refresh_corporate_actions import refresh_corporate_actions

router = Router(tags=["integrity"])


def _statuses(investor: Investor):
    return list(investor.integrity_statuses.select_related("security", "folio").all())


@router.get("/{investor_id}/integrity", response=list[IntegrityStatusOut])
def list_integrity(request, investor_id: int):
    return _statuses(get_owned_investor(request, investor_id))


@router.post(
    "/{investor_id}/integrity/refresh-corporate-actions", response=list[IntegrityStatusOut]
)
def refresh_corporate_actions_now(request, investor_id: int):
    """Fetch NSE/BSE corporate actions for this investor's mismatched equities now,
    re-reconcile, and return the updated statuses. The user-triggered counterpart to
    the daily scheduler tick — so suggestions are ready right after an import without
    waiting. Bounded to the actionable (mismatch) set."""
    investor = get_owned_investor(request, investor_id)
    sec_ids = (
        SecurityIntegrityStatus.objects.filter(
            investor=investor,
            status=IntegrityStatus.MISMATCH.value,
            security__security_type=SecurityType.EQUITY.value,
        )
        .values_list("security_id", flat=True)
        .distinct()
    )
    securities = list(Security.objects.filter(id__in=sec_ids).exclude(symbol=""))
    if securities:
        refresh_corporate_actions(securities=securities)
        for sec in securities:
            reconcile_security(investor, sec)
    return _statuses(investor)


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


def _integrity_after_apply(
    investor: Investor,
    folio: Folio,
    security: Security,
    summary: dict,
) -> SecurityIntegrityStatus:
    """Pick the post-apply integrity row when ledger rows may have moved off the path security.

    A cross-ISIN merger re-reconciles affected securities and can delete the
    (path security, folio) status once no transactions remain there; prefer any surviving
    row from ``summary["security_ids"]``, then fall back to the path pair.
    """
    for sid in reversed(summary.get("security_ids") or []):
        status = SecurityIntegrityStatus.objects.filter(
            investor=investor, security_id=sid, folio=folio
        ).first()
        if status is not None:
            return status
    return SecurityIntegrityStatus.objects.get(investor=investor, security=security, folio=folio)


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


@router.post(
    "/{investor_id}/integrity/{security_id}/{folio_id}/apply-corporate-action",
    response=ApplyCorporateActionOut,
)
def apply_corporate_action(
    request,
    investor_id: int,
    security_id: int,
    folio_id: int,
    payload: ApplyCorporateActionIn,
):
    """Apply a cached corporate-action reference to the folio ledger and re-reconcile."""
    investor = get_owned_investor(request, investor_id)
    security, folio = _owned_status(investor, security_id, folio_id)
    try:
        summary = apply_suggested_corporate_actions(
            investor, folio, security, payload.reference_ids
        )
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    return {
        **summary,
        "integrity": _integrity_after_apply(investor, folio, security, summary),
    }


@router.post(
    "/{investor_id}/integrity/{security_id}/{folio_id}/apply-manual-corporate-action",
    response=ApplyCorporateActionOut,
)
def apply_manual_corporate_action_entry(
    request,
    investor_id: int,
    security_id: int,
    folio_id: int,
    payload: ManualCorporateActionIn,
):
    """Author a corporate action by hand (bonus/split/merger/rights/buyback)
    for the flagged (security, folio), apply it, and re-reconcile."""
    investor = get_owned_investor(request, investor_id)
    security, folio = _owned_status(investor, security_id, folio_id)
    try:
        summary = apply_manual_corporate_action(investor, folio, security, **payload.dict())
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    return {
        **summary,
        "integrity": _integrity_after_apply(investor, folio, security, summary),
    }


@router.post(
    "/{investor_id}/integrity/{security_id}/{folio_id}/record-opening-lot",
    response=RecordOpeningLotOut,
)
def record_opening_lot_entry(
    request,
    investor_id: int,
    security_id: int,
    folio_id: int,
    payload: RecordOpeningLotIn,
):
    """Record a classified opening lot for an eCAS-only equity and re-reconcile."""
    investor = get_owned_investor(request, investor_id)
    security, folio = _owned_status(investor, security_id, folio_id)
    try:
        kind = OpeningLotKind(payload.classification)
    except ValueError as exc:
        raise HttpError(400, f"unknown classification: {payload.classification!r}") from exc
    try:
        summary = record_opening_lot(
            investor,
            folio,
            security,
            kind=kind,
            lot_date=payload.date,
            units=payload.units,
            price=payload.price,
            cost_basis_unknown=payload.cost_basis_unknown,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    status = SecurityIntegrityStatus.objects.get(investor=investor, security=security, folio=folio)
    return {**summary, "integrity": status}


@router.post(
    "/{investor_id}/integrity/{security_id}/{folio_id}/record-opening-lots",
    response=RecordOpeningLotsOut,
)
def record_opening_lots_entry(
    request,
    investor_id: int,
    security_id: int,
    folio_id: int,
    payload: RecordOpeningLotsIn,
):
    """Record several opening lots (a demerger receipt's per-lot allocation) and re-reconcile."""
    investor = get_owned_investor(request, investor_id)
    security, folio = _owned_status(investor, security_id, folio_id)
    try:
        kind = OpeningLotKind(payload.classification)
    except ValueError as exc:
        raise HttpError(400, f"unknown classification: {payload.classification!r}") from exc
    try:
        summary = record_opening_lots(
            investor,
            folio,
            security,
            kind=kind,
            lots=[
                {"lot_date": row.date, "units": row.units, "price": row.price}
                for row in payload.lots
            ],
            cost_basis_unknown=payload.cost_basis_unknown,
            demerger_date=payload.demerger_date,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    status = SecurityIntegrityStatus.objects.filter(
        investor=investor, security=security, folio=folio
    ).first()
    return {**summary, "integrity": status}


@router.post(
    "/{investor_id}/integrity/{security_id}/{folio_id}/apply-identity-remap",
    response=IdentityRemapOut,
)
def apply_identity_remap_entry(
    request,
    investor_id: int,
    security_id: int,
    folio_id: int,
    payload: IdentityRemapIn,
):
    """Re-point folio ledger rows to a new ISIN without changing units or cost."""
    investor = get_owned_investor(request, investor_id)
    security, folio = _owned_status(investor, security_id, folio_id)
    try:
        summary = apply_identity_remap(
            investor,
            folio,
            security,
            to_isin=payload.to_isin,
            to_symbol=payload.to_symbol,
            to_name=payload.to_name,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    target = Security.objects.get(pk=summary["target_security_id"])
    status = SecurityIntegrityStatus.objects.filter(
        investor=investor, security=target, folio=folio
    ).first()
    if status is None:
        status = SecurityIntegrityStatus.objects.get(
            investor=investor, security=security, folio=folio
        )
    return {**summary, "integrity": status}
