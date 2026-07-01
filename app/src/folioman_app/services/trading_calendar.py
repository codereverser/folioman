"""Business-day helpers for NAV freshness — NAVs publish on trading days.

Shared by the NAV-staleness marker (``services.valuation``) and the history backfill
(``tasks.refresh_navs``). Public holidays aren't modelled; callers apply a small grace
(one trading day) to absorb the common "today's NAV isn't out yet / a fund declared
late" case.
"""

from __future__ import annotations

from datetime import date, timedelta


def last_trading_day(d: date) -> date:
    """Most recent weekday on/before ``d`` (Sat/Sun roll back to Friday)."""
    while d.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        d -= timedelta(days=1)
    return d


def trading_days_between(start: date, end: date) -> int:
    """Count weekdays in ``(start, end]`` — 0 when ``end <= start``."""
    days = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days


def trading_days(start: date, end: date):
    """Yield each weekday in ``[start, end]`` inclusive (holidays not modelled)."""
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            yield cur
        cur += timedelta(days=1)
