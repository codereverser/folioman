"""Jobs router: advisor-wide import + valuation activity for the Settings panel.

Read-only. Returns the recent import jobs across all the advisor's investors plus
each investor's day-wise valuation status, with the real per-security cause behind a
failure (closed/matured, unmapped ISIN, or feed-pending) rather than the generic
"N securities awaiting NAV". Scoped to the authenticated advisor via ``investors_for``.
"""

from __future__ import annotations

from ninja import Router

from folioman_app.api.auth import investors_for
from folioman_app.api.schemas import JobsOverviewOut
from folioman_app.services.jobs import build_jobs_overview

router = Router(tags=["jobs"])


@router.get("/jobs", response=JobsOverviewOut)
def jobs_overview(request):
    """Recent import jobs + per-investor valuation status/errors for the advisor."""
    return build_jobs_overview(investors_for(request))
