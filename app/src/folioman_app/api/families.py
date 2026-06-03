"""Families router: CRUD + combined aggregate valuation.

Every query is scoped to the authenticated advisor (``owned_by``) via the
helpers in ``api.auth``. Deleting a family demotes its investors to solo
(Investor.family is SET_NULL) — their data is untouched. The aggregate is
family-level valuation only; tax exports stay per-investor (each PAN files its
own ITR).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from django.db.models import Count
from ninja import Query, Router, Status

from folioman_app.api.auth import families_for, get_owned_family
from folioman_app.api.schemas import (
    FamilyAggregateOut,
    FamilyDetailOut,
    FamilyIn,
    FamilyOut,
    InvestorOut,
    ValuationStatusOut,
    ValueSeriesOut,
)
from folioman_app.models import Family
from folioman_app.services.valuation import (
    build_family_aggregate,
    build_family_valuation_status,
    default_series_start,
    family_value_series,
)

router = Router(tags=["families"])


def _with_count(qs):
    return qs.annotate(investor_count=Count("investors"))


@router.get("/", response=list[FamilyOut])
def list_families(request):
    return list(_with_count(families_for(request)))


@router.post("/", response={201: FamilyOut})
def create_family(request, payload: FamilyIn):
    family = Family.objects.create(owned_by=request.auth, name=payload.name)
    family.investor_count = 0
    return Status(201, family)


@router.get("/{family_id}", response=FamilyDetailOut)
def get_family(request, family_id: int):
    family = get_owned_family(request, family_id)
    return FamilyDetailOut(
        id=family.id,
        name=family.name,
        investor_count=family.investors.count(),
        created_at=family.created_at,
        investors=[InvestorOut.from_orm(i) for i in family.investors.all()],
    )


@router.patch("/{family_id}", response=FamilyOut)
def update_family(request, family_id: int, payload: FamilyIn):
    family = get_owned_family(request, family_id)
    family.name = payload.name
    family.save()
    family.investor_count = family.investors.count()
    return family


@router.delete("/{family_id}", response={204: None})
def delete_family(request, family_id: int):
    get_owned_family(request, family_id).delete()
    return Status(204, None)


@router.get("/{family_id}/aggregate", response=FamilyAggregateOut)
def family_aggregate(request, family_id: int, as_of: date | None = None):
    family = get_owned_family(request, family_id)
    return build_family_aggregate(family, as_of or date.today())


@router.get("/{family_id}/value-series", response=ValueSeriesOut)
def family_value_series_endpoint(
    request,
    family_id: int,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    granularity: Literal["daily", "weekly", "monthly"] = "monthly",
):
    """Family-wide net-worth-over-time, aggregated across the family's investors."""
    family = get_owned_family(request, family_id)
    end = to or date.today()
    start = from_ or default_series_start(end)
    points = family_value_series(family, from_=start, to=end, granularity=granularity)
    return {
        "family_id": family.id,
        "start": start,
        "end": end,
        "granularity": granularity,
        "points": points,
    }


@router.get("/{family_id}/valuation-status", response=ValuationStatusOut)
def family_valuation_status(request, family_id: int):
    """Combined readiness — ready only when every member's day-wise valuation is."""
    family = get_owned_family(request, family_id)
    return build_family_valuation_status(family)
