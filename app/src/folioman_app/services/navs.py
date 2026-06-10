"""NAV freshness overview for the Settings panel.

Answers "how current are my prices?" per security: the latest stored NAV date,
how many trading days it lags the last *completed* trading day, and the stored
history range. Read-only — refreshes run on the scheduler (every 6 hours, see
``tasks.valuation_ticks.REVALUE_HOURS``) or via ``manage.py refresh_navs``; the
panel shows the schedule instead of offering a trigger.

The baseline is the previous trading day, not today: a NAV/close for trading
day T publishes after T ends (MF NAVs late evening to next morning), so on a
Wednesday the freshest a feed can be is Tuesday's value. Measuring against
today would mark the whole book "1 day behind" every day.

Freshness classification:

- ``fresh``    latest NAV is on the last completed trading day
- ``grace``    one trading day behind it (a fund declaring late)
- ``stale``    more than one trading day behind
- ``pending``  feed code exists but no NAV stored yet (transient)
- ``closed``   feed confirmed dead (matured/delisted) — valued at last known
- ``no_feed``  nothing to query (unmapped ISIN, or a type with no feed, e.g. FD)
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.db.models import Count, Max, Min
from django.utils import timezone
from folioman_core.models import SecurityType

from folioman_app.models import NAVHistory, Security
from folioman_app.services.trading_calendar import last_trading_day, trading_days_between
from folioman_app.tasks.valuation_ticks import REVALUE_HOURS

_QUOTE_TYPES = {
    SecurityType.EQUITY.value,
    SecurityType.ETF.value,
    SecurityType.BOND.value,
    SecurityType.FOREIGN_EQUITY.value,
}


def _completed_trading_day(today) -> object:
    """The most recent trading day whose NAVs can exist — always before today."""
    return last_trading_day(today - timedelta(days=1))


def _feed_code(security: Security) -> str:
    """The identifier the refresh would query for this security, or ""."""
    stype = security.security_type
    if stype == SecurityType.MF.value:
        return security.amfi_code
    if stype in _QUOTE_TYPES:
        return security.symbol
    if stype == SecurityType.CRYPTO.value:
        return (security.metadata or {}).get("coin_id") or ""
    return ""  # FD etc.: no live feed


def _classify(security: Security, latest, lag: int | None) -> str:
    if security.nav_feed_closed:
        return "closed"
    if not _feed_code(security):
        return "no_feed"
    if latest is None:
        return "pending"
    if lag is None or lag <= 0:
        return "fresh"
    if lag == 1:
        return "grace"
    return "stale"


def next_scheduled_refresh() -> datetime:
    """The next scheduler NAV re-fetch (the 6-hourly revalue cron), local time."""
    now = timezone.localtime()
    for hour in sorted(REVALUE_HOURS):
        if now.hour < hour:
            return now.replace(hour=hour, minute=0, second=0, microsecond=0)
    tomorrow = now.date() + timedelta(days=1)
    first = sorted(REVALUE_HOURS)[0]
    return timezone.make_aware(datetime.combine(tomorrow, time(hour=first)))


def build_nav_freshness(investors) -> dict:
    """Per-security freshness for the given investors' book, worst-lag first."""
    sec_ids: set[int] = set()
    for inv in investors:
        sec_ids |= set(inv.transactions.values_list("security_id", flat=True))
        sec_ids |= set(inv.holdings.values_list("security_id", flat=True))

    stats = {
        row["security_id"]: row
        for row in NAVHistory.objects.filter(security_id__in=sec_ids)
        .values("security_id")
        .annotate(latest=Max("date"), first=Min("date"), points=Count("id"))
    }
    cutoff = _completed_trading_day(timezone.localdate())
    _order = {"stale": 0, "pending": 1, "closed": 2, "grace": 3, "no_feed": 4, "fresh": 5}

    rows = []
    for security in Security.objects.filter(id__in=sec_ids):
        stat = stats.get(security.id)
        latest = stat["latest"] if stat else None
        lag = trading_days_between(latest, cutoff) if latest is not None else None
        status = _classify(security, latest, lag)
        rows.append(
            {
                "security_id": security.id,
                "name": security.name,
                "security_type": security.security_type,
                "identifier": security.symbol or security.isin or security.amfi_code,
                "feed_code": _feed_code(security),
                "latest_nav_date": latest,
                "first_nav_date": stat["first"] if stat else None,
                "points": stat["points"] if stat else 0,
                "lag_trading_days": lag,
                "status": status,
            }
        )
    rows.sort(key=lambda r: (_order[r["status"]], -(r["lag_trading_days"] or 0), r["name"]))
    # last_refreshed_at: when a price row was last *written* (any source — the
    # scheduler pass, a backfill, an import). The "is the pipeline alive?" signal.
    last_written = NAVHistory.objects.filter(security_id__in=sec_ids).aggregate(
        m=Max("updated_at")
    )["m"]
    return {
        "as_of": cutoff,
        "securities": rows,
        "last_refreshed_at": last_written,
        "next_refresh_at": next_scheduled_refresh(),
    }
