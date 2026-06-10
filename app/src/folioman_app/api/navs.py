"""NAVs router: per-security price freshness, scoped to the advisor's book
(securities appearing in their investors' holdings or transactions).

Read-only by design: NAV refreshes run on the scheduler (every 6 hours) or via
``manage.py refresh_navs`` — there is deliberately no refresh trigger here, so
a user can't hammer the public feeds from the UI. The response carries the
schedule so the panel can say when the next pass runs.
"""

from __future__ import annotations

from ninja import Router

from folioman_app.api.auth import investors_for
from folioman_app.api.schemas import NavFreshnessOut
from folioman_app.services.navs import build_nav_freshness

router = Router(tags=["navs"])


@router.get("/navs/freshness", response=NavFreshnessOut)
def nav_freshness(request):
    """Every tracked security's latest NAV date + trading-day lag, worst first."""
    return build_nav_freshness(investors_for(request))
