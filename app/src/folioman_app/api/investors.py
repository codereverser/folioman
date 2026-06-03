"""Investors router: CRUD + folio listing, family-scoped filtering.

Every query is scoped to the authenticated advisor (``owned_by``) via the
helpers in ``api.auth`` — a missing *or* cross-advisor id returns 404 alike, so
ownership never leaks. PATCH applies only the fields present in the request body.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from django.conf import settings
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Status
from ninja.errors import HttpError
from pydantic import ValidationError

from folioman_app.api.auth import get_owned_family, get_owned_investor, investors_for
from folioman_app.api.schemas import (
    FolioOut,
    InvestorIn,
    InvestorOut,
    InvestorSummaryOut,
    InvestorUpdate,
    SchemeDetailOut,
    TransactionIn,
    TransactionOut,
    ValuationStatusOut,
    ValueSeriesOut,
)
from folioman_app.models import Investor, Security
from folioman_app.services.valuation import (
    build_investor_summary,
    build_scheme_detail,
    build_valuation_status,
    default_series_start,
    value_series,
)
from folioman_app.tasks.import_csv import create_manual_transaction

router = Router(tags=["investors"])


@router.get("/", response=list[InvestorOut])
def list_investors(request, family_id: int | None = None, unaffiliated: bool = False):
    qs = investors_for(request)
    if unaffiliated:
        qs = qs.filter(family__isnull=True)
    elif family_id is not None:
        qs = qs.filter(family_id=family_id)
    return list(qs)


@router.post("/", response={201: InvestorOut})
def create_investor(request, payload: InvestorIn):
    data = payload.model_dump(exclude={"pan", "family_id"})
    investor = Investor(owned_by=request.auth, **data)
    if payload.family_id is not None:
        investor.family = get_owned_family(request, payload.family_id)
    if payload.pan:
        investor.set_pan(payload.pan)
    investor.save()
    return Status(201, investor)


@router.get("/{investor_id}", response=InvestorOut)
def get_investor(request, investor_id: int):
    return get_owned_investor(request, investor_id)


@router.patch("/{investor_id}", response=InvestorOut)
def update_investor(request, investor_id: int, payload: InvestorUpdate):
    investor = get_owned_investor(request, investor_id)
    data = payload.model_dump(exclude_unset=True)
    if "pan" in data:
        investor.set_pan(data.pop("pan"))
    if "family_id" in data:
        family_id = data.pop("family_id")
        investor.family = get_owned_family(request, family_id) if family_id is not None else None
    for field, value in data.items():
        setattr(investor, field, value)
    investor.save()
    return investor


@router.delete("/{investor_id}", response={204: None})
def delete_investor(request, investor_id: int):
    get_owned_investor(request, investor_id).delete()
    return Status(204, None)


@router.get("/{investor_id}/summary", response=InvestorSummaryOut)
def investor_summary(request, investor_id: int, as_of: date | None = None):
    """Roster headline numbers: current INR value, tax-ready vs total holdings,
    items needing attention, and the last successful import date."""
    investor = get_owned_investor(request, investor_id)
    return build_investor_summary(investor, as_of or date.today())


@router.get("/{investor_id}/value-series", response=ValueSeriesOut)
def investor_value_series(
    request,
    investor_id: int,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    granularity: Literal["daily", "weekly", "monthly"] = "monthly",
):
    """Net-worth-over-time, reconstructed from the ledger + NAV history. The final
    point matches the point-in-time ``/summary`` value for the same ``to`` date."""
    investor = get_owned_investor(request, investor_id)
    end = to or date.today()
    start = from_ or default_series_start(end)
    points = value_series(investor, from_=start, to=end, granularity=granularity)
    return {
        "investor_id": investor.id,
        "start": start,
        "end": end,
        "granularity": granularity,
        "points": points,
    }


@router.get("/{investor_id}/valuation-status", response=ValuationStatusOut)
def investor_valuation_status(request, investor_id: int):
    """Whether the day-wise valuation is ready (chart gate) or still computing —
    the dashboard polls this and shows a placeholder + provisional value meanwhile."""
    investor = get_owned_investor(request, investor_id)
    return build_valuation_status(investor)


@router.get("/{investor_id}/holdings/{security_id}", response=SchemeDetailOut)
def scheme_detail(request, investor_id: int, security_id: int, as_of: date | None = None):
    """One scheme's detail: identity, metrics, integrity, NAV history, ledger.
    404 if the investor has never held or transacted this security."""
    investor = get_owned_investor(request, investor_id)
    security = get_object_or_404(Security, id=security_id)
    held = (
        investor.transactions.filter(security=security).exists()
        or investor.holdings.filter(security=security).exists()
    )
    if not held:
        raise HttpError(404, "investor has no holding for this security")
    return build_scheme_detail(investor, security, as_of or date.today())


@router.get("/{investor_id}/folios", response=list[FolioOut])
def list_folios(request, investor_id: int):
    investor = get_owned_investor(request, investor_id)
    return list(investor.folios.all())


@router.get("/{investor_id}/transactions", response=list[TransactionOut])
def list_transactions(request, investor_id: int):
    investor = get_owned_investor(request, investor_id)
    return list(investor.transactions.all())


@router.post("/{investor_id}/transactions", response={201: TransactionOut})
def create_transaction(request, investor_id: int, payload: TransactionIn):
    """Manually add one transaction (security identified inline, upserted).

    Gated off by default in the first release (CAS/eCAS imports only). Flip
    ``FOLIOMAN_MANUAL_TXNS=1`` to enable; the logic below is unchanged.
    """
    if not settings.MANUAL_TRANSACTIONS_ENABLED:
        raise HttpError(503, "Manual transaction entry isn't available yet — import a CAS instead.")
    investor = get_owned_investor(request, investor_id)
    try:
        txn = create_manual_transaction(investor, payload.model_dump())
    except (ValueError, ValidationError) as exc:
        raise HttpError(422, str(exc)) from exc
    return Status(201, txn)
