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

from collections.abc import Iterable
from datetime import date as date_cls

from django.db.models import Min
from folioman_core.models import SecurityType
from folioman_core.price_feeds import coingecko, mfapi, nse_bse, yfinance_feed
from folioman_core.price_feeds.mfapi import NAVFetchError
from folioman_core.price_feeds.yfinance_feed import PriceFetchError

from folioman_app.models import NAVHistory, Security, Transaction

_QUOTE_TYPES = {
    SecurityType.EQUITY.value,
    SecurityType.ETF.value,
    SecurityType.BOND.value,
    SecurityType.FOREIGN_EQUITY.value,
}


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
    for security in qs:
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
    """Backfill history for MF securities, each bounded by its earliest transaction."""
    qs = (
        securities
        if securities is not None
        else Security.objects.filter(security_type=SecurityType.MF.value)
    )
    summary = {"securities": 0, "points": 0, "errors": 0}
    for security in qs:
        since = Transaction.objects.filter(security=security).aggregate(first=Min("date"))["first"]
        try:
            written = backfill_nav_history(security, since=since)
        except (NAVFetchError, PriceFetchError):
            summary["errors"] += 1
            continue
        if written:
            summary["securities"] += 1
            summary["points"] += written
    return summary
