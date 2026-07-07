"""Reports router (per investor): recurring-income view + the per-FY series that
drive the year-over-year charts on the Income and Capital Gains pages.

Read-only and owner-scoped, like the exports router. The income report is a draft
to review with your tax professional (ITR Schedule OS), never a filed return.
"""

from __future__ import annotations

from django.http import HttpResponse
from ninja import Router
from ninja.errors import HttpError

from folioman_app.api.auth import get_owned_investor
from folioman_app.api.schemas import (
    CapitalGainsFyPoint,
    IncomeFyPoint,
    IncomeReportOut,
)
from folioman_app.services.income import build_income_by_fy, build_income_csv, build_income_report
from folioman_app.services.tax_export import build_capital_gains_by_fy

router = Router(tags=["reports"])


@router.get("/{investor_id}/reports/income", response=IncomeReportOut)
def income(request, investor_id: int, fy: str):
    """Recurring income for one FY — dividends grouped by kind, with a 234C
    quarterly split. A read to review, not a filed return."""
    investor = get_owned_investor(request, investor_id)
    try:
        return build_income_report(investor, fy)
    except ValueError as exc:  # e.g. a malformed FY label
        raise HttpError(422, str(exc)) from exc


@router.get("/{investor_id}/reports/income-by-fy", response=list[IncomeFyPoint])
def income_by_fy(request, investor_id: int):
    """Income totals across every FY with data (drives the stacked bar chart)."""
    investor = get_owned_investor(request, investor_id)
    return build_income_by_fy(investor)


@router.get("/{investor_id}/reports/capital-gains-by-fy", response=list[CapitalGainsFyPoint])
def capital_gains_by_fy(request, investor_id: int, include_unreconciled: bool = False):
    """Realised STCG/LTCG across every FY with a disposal (drives the CG chart)."""
    investor = get_owned_investor(request, investor_id)
    return build_capital_gains_by_fy(investor, include_unreconciled=include_unreconciled)


@router.get("/{investor_id}/reports/income.csv")
def income_csv(request, investor_id: int, fy: str, basis: str = "accrued"):
    """Income for one FY as a Schedule OS-shaped CSV, on the requested basis."""
    investor = get_owned_investor(request, investor_id)
    try:
        text = build_income_csv(investor, fy, basis=basis)
    except ValueError as exc:
        raise HttpError(422, str(exc)) from exc
    response = HttpResponse(text, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="income_{investor_id}_{fy}.csv"'
    return response
