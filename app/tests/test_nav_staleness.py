"""NAV-feed staleness (V9): the business-day-aware 'old-but-present prices' marker.

Distinct from the unpriced gap — here the prices exist but are simply old because the
feed hasn't run. The marker must fire on a real lag (2+ trading days) but not on a
weekend, when the latest NAV is just the last trading day's.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import NAVHistory
from folioman_app.services.valuation import _last_trading_day, _navs_stale
from folioman_core.models import SecurityType

# A reference week: Mon 2025-06-02 … Fri 06-06, Sat 06-07, Sun 06-08, next Mon 06-09.
MON, TUE, WED, THU, FRI = (dt.date(2025, 6, d) for d in (2, 3, 4, 5, 6))
SAT, SUN, NEXT_MON = dt.date(2025, 6, 7), dt.date(2025, 6, 8), dt.date(2025, 6, 9)


def test_last_trading_day_rolls_back_over_the_weekend():
    assert _last_trading_day(SAT) == FRI
    assert _last_trading_day(SUN) == FRI
    assert _last_trading_day(WED) == WED


@pytest.mark.parametrize(
    ("navs_as_of", "as_of", "stale"),
    [
        (FRI, SAT, False),  # weekend: Friday's NAV is still the latest expected
        (FRI, SUN, False),
        (FRI, NEXT_MON, False),  # 1 trading day behind — today's NAV isn't out yet (grace)
        (WED, FRI, True),  # 2 trading days old — the feed lagged
        (THU, NEXT_MON, True),  # missed Friday, then the weekend — 2 trading days behind
        (None, WED, False),  # nothing priced yet — that's the unpriced case, not staleness
    ],
)
def test_navs_stale_is_business_day_aware(navs_as_of, as_of, stale):
    assert _navs_stale(navs_as_of, as_of) is stale


@pytest.mark.django_db
def test_summary_flags_a_two_business_day_old_feed(
    client, make_investor, make_security, make_holding
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    make_holding(investor=inv, security=mf, units=Decimal("100"), as_of_date=WED)
    NAVHistory.objects.create(security=mf, date=WED, nav=Decimal("50"))  # Wed, valued on Fri

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-06"}).json()  # Fri

    assert body["navs_as_of"] == "2025-06-04"
    assert body["navs_stale"] is True


@pytest.mark.django_db
def test_summary_does_not_flag_a_weekend_gap(client, make_investor, make_security, make_holding):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    make_holding(investor=inv, security=mf, units=Decimal("100"), as_of_date=FRI)
    NAVHistory.objects.create(security=mf, date=FRI, nav=Decimal("50"))  # Fri, viewed on Sat

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-07"}).json()  # Sat

    assert body["navs_as_of"] == "2025-06-06"
    assert body["navs_stale"] is False
