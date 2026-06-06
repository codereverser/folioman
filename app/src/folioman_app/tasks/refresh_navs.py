"""Refresh latest NAV / price for priceable securities into NAVHistory.

Dispatches by security type to the core price feeds (best-effort — a feed
outage for one security is recorded and the rest continue):
- MF (amfi_code)              -> mfapi
- equity/etf/bond (symbol)    -> Yahoo, then the NSE fallback (Indian only)
- foreign_equity (symbol)     -> Yahoo (kept in native currency; FX is v2)
- crypto (metadata.coin_id)   -> CoinGecko (INR)

Run on demand (`manage.py refresh_navs`) or from the OS scheduler.
Historical backfill on first import is covered by the backfill helpers here.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import date as date_cls

from django.db.models import Max, Min
from django.utils import timezone
from folioman_core.models import SecurityType
from folioman_core.price_feeds import coingecko, mfapi, nse_bse, yfinance_feed
from folioman_core.price_feeds.mfapi import NAVFetchError
from folioman_core.price_feeds.yfinance_feed import PriceFetchError

from folioman_app.models import NAVHistory, Security, Transaction
from folioman_app.services.trading_calendar import last_trading_day, trading_days_between

_QUOTE_TYPES = {
    SecurityType.EQUITY.value,
    SecurityType.ETF.value,
    SecurityType.BOND.value,
    SecurityType.FOREIGN_EQUITY.value,
}

# Politeness: a small gap between consecutive feed calls so a batch refresh /
# backfill doesn't hammer a free public API. Indirected through ``_SLEEP`` so
# tests stub it; ``_REQUEST_SPACING`` is module-level so a caller can tune it.
_REQUEST_SPACING = 0.15  # seconds between live fetches
# Backfill grace: skip a fund whose latest NAV is within this many *trading* days of
# the last trading day (today's NAV may not be out yet / a fund can declare late).
# Beyond it we re-pull the full history and fill every missing date — so a desktop
# opened once a fortnight catches up gaplessly instead of leaving weeks of holes.
_HISTORY_FRESH_TRADING_DAYS = 1
_SLEEP = time.sleep


def _fetch_point(security: Security):
    """Return (date, nav, source) for the latest price, or None if unavailable."""
    stype = security.security_type
    if stype == SecurityType.MF.value and security.amfi_code:
        point = mfapi.fetch_latest_nav(security.amfi_code)
        return (point.date, point.nav, "mfapi") if point else None
    if stype in _QUOTE_TYPES and security.symbol:
        quote = yfinance_feed.fetch_quote(security.symbol, exchange=security.exchange)
        if quote is None and stype != SecurityType.FOREIGN_EQUITY.value:
            quote = nse_bse.fetch_quote(security.symbol)  # NSE fallback (Indian)
        return (quote.as_of, quote.price, quote.source) if quote else None
    if stype == SecurityType.CRYPTO.value:
        coin_id = (security.metadata or {}).get("coin_id")
        if coin_id:
            quote = coingecko.fetch_quote(coin_id)
            return (quote.as_of, quote.price, quote.source) if quote else None
    return None


def refresh_navs(*, securities: Iterable[Security] | None = None) -> dict:
    qs = Security.objects.all() if securities is None else securities
    summary = {"updated": 0, "skipped": 0, "errors": 0}
    fetched = False
    for security in qs:
        if fetched:
            _SLEEP(_REQUEST_SPACING)  # space consecutive live calls
        fetched = True
        try:
            point = _fetch_point(security)
        except (NAVFetchError, PriceFetchError):
            summary["errors"] += 1
            continue
        if point is None:
            summary["skipped"] += 1  # no feed for this type, or no data
            continue
        on, nav, source = point
        NAVHistory.objects.update_or_create(
            security=security, date=on, defaults={"nav": nav, "source": source}
        )
        summary["updated"] += 1
    return summary


# --- historical backfill -------------------------------------------
# Full per-scheme NAV history (mfapi serves it). Only MF has a history feed in
# v1 — equity/crypto history would need range endpoints the core doesn't expose
# yet, so they keep just the latest point from refresh_navs.
#
# Backfill is a separate, resilient step (this service + the backfill_navs
# command / the scheduler), NOT wired into the synchronous import: an import must
# not block on or fail because a history feed is slow/down. Idempotent — only the
# dates not already present are inserted.


def backfill_nav_history(security: Security, *, since: date_cls | None = None) -> int:
    """Backfill an MF security's NAV history into NAVHistory. Returns points written."""
    if security.security_type != SecurityType.MF.value or not security.amfi_code:
        return 0
    history = mfapi.fetch_nav_history(security.amfi_code, since=since)
    existing = set(NAVHistory.objects.filter(security=security).values_list("date", flat=True))
    to_create = [
        NAVHistory(security=security, date=point.date, nav=point.nav, source="mfapi")
        for point in history.points
        if point.date not in existing
    ]
    NAVHistory.objects.bulk_create(to_create)
    return len(to_create)


def backfill_missing_history(*, securities: Iterable[Security] | None = None) -> dict:
    """Backfill history for MF securities, each bounded by its earliest transaction.

    Whenever a fund's latest NAV is more than a trading day behind the last trading
    day, re-pull the full history (mfapi serves it all) and insert every missing date
    — so the series stays gap-free from the last stored date to today even if the app
    only ran days or weeks ago. A fund already current (within the grace) is skipped
    to avoid re-downloading; the cheap latest-point refresh keeps it warm meanwhile."""
    qs = (
        securities
        if securities is not None
        else Security.objects.filter(security_type=SecurityType.MF.value)
    )
    summary = {"securities": 0, "points": 0, "errors": 0, "skipped": 0, "closed": 0}
    today = timezone.localdate()
    cutoff = last_trading_day(today)
    fetched = False
    for security in qs:
        # Skip only when the series already reaches (within a grace of) the last
        # trading day — otherwise re-pull and fill the gap. Business-day aware so we
        # don't refetch every weekend or for a not-yet-published same-day NAV; gap
        # aware so a fortnight-old series is brought fully current, not just nudged.
        latest = NAVHistory.objects.filter(security=security).aggregate(m=Max("date"))["m"]
        behind = trading_days_between(latest, cutoff) if latest is not None else None
        if behind is not None and behind <= _HISTORY_FRESH_TRADING_DAYS:
            summary["skipped"] += 1
            continue
        if fetched:
            _SLEEP(_REQUEST_SPACING)  # space consecutive live calls
        fetched = True
        since = Transaction.objects.filter(security=security).aggregate(first=Min("date"))["first"]
        try:
            written = backfill_nav_history(security, since=since)
        except (NAVFetchError, PriceFetchError):
            summary["errors"] += 1  # transient: leave it feed-pending, retry next cycle
            continue
        if written:
            summary["securities"] += 1
            summary["points"] += written
            if security.nav_feed_closed:  # data arrived → reopen a previously-dead code
                security.nav_feed_closed = False
                security.save(update_fields=["nav_feed_closed", "updated_at"])
        elif latest is None and not security.nav_feed_closed:
            # The feed responded (no error) with no history for a fund we hold NO NAV
            # for: the code is dead (matured/delisted/unmappable), not slow. Flag it so
            # valuation degrades it instead of erroring + retrying the feed forever.
            security.nav_feed_closed = True
            security.save(update_fields=["nav_feed_closed", "updated_at"])
            summary["closed"] += 1
    return summary
