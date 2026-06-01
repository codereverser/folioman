"""Exports router (per investor). Schedule 112A, plus holdings / transactions CSV.

The 112A export is the paid Tax Pack feature. Licensing is stubbed here (always
allowed) until licensing enforcement wires `tax_export` feature-gating (403 when unlicensed).
"""

from __future__ import annotations

from django.http import HttpResponse
from ninja import Router
from ninja.errors import HttpError

from folioman_app.api.auth import get_owned_investor
from folioman_app.api.schemas import Schedule112ARequest, Schedule112AResponse
from folioman_app.services.exports import build_holdings_csv, build_transactions_csv
from folioman_app.services.tax_export import build_schedule_112a

router = Router(tags=["exports"])


def _csv_response(text: str, filename: str) -> HttpResponse:
    response = HttpResponse(text, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@router.get("/{investor_id}/exports/holdings")
def export_holdings(request, investor_id: int):
    """Current holdings + valuation as a downloadable CSV (free tier)."""
    investor = get_owned_investor(request, investor_id)
    return _csv_response(build_holdings_csv(investor), f"holdings_{investor_id}.csv")


@router.get("/{investor_id}/exports/transactions")
def export_transactions(request, investor_id: int):
    """Full transaction ledger as a CSV in the import format (round-trippable)."""
    investor = get_owned_investor(request, investor_id)
    return _csv_response(build_transactions_csv(investor), f"transactions_{investor_id}.csv")


@router.post("/{investor_id}/exports/schedule-112a", response=Schedule112AResponse)
def schedule_112a(request, investor_id: int, payload: Schedule112ARequest):
    investor = get_owned_investor(request, investor_id)
    # TODO: gate on the `tax_export` license feature (403 if unlicensed).
    try:
        return build_schedule_112a(
            investor, payload.fy, include_unreconciled=payload.include_unreconciled
        )
    except ValueError as exc:  # e.g. an out-of-range FY label
        raise HttpError(422, str(exc)) from exc
