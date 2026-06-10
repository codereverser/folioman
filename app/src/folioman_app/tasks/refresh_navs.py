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

import functools
import logging
import time
from collections.abc import Iterable
from datetime import date as date_cls
from datetime import timedelta

from django.db.models import Max, Min
from django.utils import timezone
from folioman_core.models import SecurityType
from folioman_core.price_feeds import coingecko, mfapi, nse_history, yfinance_feed
from folioman_core.price_feeds.mfapi import NAVFetchError
from folioman_core.price_feeds.yfinance_feed import PriceFetchError

from folioman_app.models import Holding, NAVHistory, Security, Transaction
from folioman_app.services.trading_calendar import last_trading_day, trading_days_between

logger = logging.getLogger(__name__)

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
# Window pulled to read an equity's *latest* close off the last row of its NSE
# security-wise history — wide enough to clear a long weekend / holiday run.
_QUOTE_LOOKBACK_DAYS = 10
_SLEEP = time.sleep


class _FeedClients:
    """Lazily-created shared HTTP clients for one batch pass.

    One pooled connection per feed instead of a fresh TLS handshake per
    security, and ONE NSE cookie warm-up per pass instead of one per equity
    (the warm-up is itself a request — per-security warming is what hammers
    NSE). Lazy, so an MF-only pass never touches NSE and a quote-only pass
    never connects to mfapi. The owning batch closes via :meth:`close`.
    """

    def __init__(self):
        self._mfapi = None
        self._nse = None
        self._yahoo = None

    @property
    def mfapi(self):
        if self._mfapi is None:
            self._mfapi = mfapi.shared_client()
        return self._mfapi

    @property
    def nse(self):
        if self._nse is None:
            self._nse = nse_history.warmed_client()
        return self._nse

    @property
    def yahoo(self):
        if self._yahoo is None:
            self._yahoo = yfinance_feed.shared_client()
        return self._yahoo

    def close(self) -> None:
        for client in (self._mfapi, self._nse, self._yahoo):
            if client is not None:
                client.close()


def _fetch_point(security: Security, clients: _FeedClients):
    """Return (date, nav, source) for the latest price, or None if unavailable."""
    stype = security.security_type
    if stype == SecurityType.MF.value and security.amfi_code:
        point = mfapi.fetch_latest_nav(security.amfi_code, client=clients.mfapi)
        return (point.date, point.nav, "mfapi") if point else None
    if stype in _QUOTE_TYPES and security.symbol:
        # NSE-first for Indian names: take the latest close from the last row of the
        # cookie-warmed security-wise history CSV. NSE's /api/quote-equity 403s from
        # cloud IPs, so reusing the proven history endpoint keeps quotes off the
        # rate-limited Yahoo fallback. Yahoo *raises* on a 429, so leading with it
        # would skip the fallback; it trails and covers BSE-only / foreign names.
        if stype != SecurityType.FOREIGN_EQUITY.value and security.exchange in ("", "NSE"):
            since = last_trading_day(timezone.localdate()) - timedelta(days=_QUOTE_LOOKBACK_DAYS)
            try:
                history = nse_history.fetch_history(
                    security.symbol, start=since, client=clients.nse
                )
            except PriceFetchError:
                history = None
            if history is not None and history.points:
                latest = history.points[-1]  # points are oldest-first
                return (latest.date, latest.nav, "nse")
        quote = yfinance_feed.fetch_quote(
            security.symbol, exchange=security.exchange, client=clients.yahoo
        )
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
    clients = _FeedClients()
    try:
        for security in qs:
            if fetched:
                _SLEEP(_REQUEST_SPACING)  # space consecutive live calls
            fetched = True
            try:
                point = _fetch_point(security, clients)
            except (NAVFetchError, PriceFetchError) as exc:
                logger.warning(
                    "NAV refresh failed for security %s (%s): %s", security.id, security.name, exc
                )
                summary["errors"] += 1
                continue
            if point is None:
                logger.debug(
                    "NAV refresh: no feed for security %s (%s)", security.id, security.name
                )
                summary["skipped"] += 1  # no feed for this type, or no data
                continue
            on, nav, source = point
            NAVHistory.objects.update_or_create(
                security=security, date=on, defaults={"nav": nav, "source": source}
            )
            summary["updated"] += 1
    finally:
        clients.close()
    return summary


# --- historical backfill -------------------------------------------
# MF history comes from mfapi (full per-scheme series in one call); equity / ETF
# / bond history comes from Yahoo's dated chart range. Crypto still keeps only
# the latest point from refresh_navs (no history feed wired yet). All land in the
# shared NAVHistory table, so a quote-type holding prices into the day-wise
# valuation series exactly like an MF.
#
# Backfill is a separate, resilient step (this service + the backfill_navs
# command / the scheduler), NOT wired into the synchronous import: an import must
# not block on or fail because a history feed is slow/down. Idempotent — only the
# dates not already present are inserted.


def backfill_nav_history(security: Security, *, since: date_cls | None = None, client=None) -> int:
    """Backfill an MF security's NAV history into NAVHistory. Returns points written.

    ``client`` lets a batch reuse one pooled mfapi connection; when ``None``
    the feed opens (and closes) its own."""
    if security.security_type != SecurityType.MF.value or not security.amfi_code:
        return 0
    history = mfapi.fetch_nav_history(security.amfi_code, since=since, client=client)
    existing = set(NAVHistory.objects.filter(security=security).values_list("date", flat=True))
    to_create = [
        NAVHistory(security=security, date=point.date, nav=point.nav, source="mfapi")
        for point in history.points
        if point.date not in existing
    ]
    NAVHistory.objects.bulk_create(to_create)
    return len(to_create)


def _fetch_equity_history(
    security: Security, *, since: date_cls | None, nse_client, yahoo_client=None
):
    """Fetch a quote-type security's price history, NSE-first with a Yahoo fallback.

    Returns ``(history, source_tag)``. NSE's security-wise feed is authoritative
    for NSE-listed names and a different provider than Yahoo (which throttles), so
    it leads; Yahoo covers BSE-only / foreign names and fills in when NSE is
    unavailable or returns nothing. ``nse_client`` lets a batch reuse one warmed
    NSE session (cookie wall) and ``yahoo_client`` one pooled Yahoo connection;
    when ``None`` each feed opens its own."""
    if security.exchange in ("", "NSE"):
        try:
            history = nse_history.fetch_history(security.symbol, start=since, client=nse_client)
        except PriceFetchError:
            history = None
        if history is not None and history.points:
            return history, "nse"
    return (
        yfinance_feed.fetch_history(
            security.symbol, exchange=security.exchange, start=since, client=yahoo_client
        ),
        "yfinance",
    )


def backfill_equity_history(
    security: Security, *, since: date_cls | None = None, nse_client=None, yahoo_client=None
) -> int:
    """Backfill a quote-type security's price history into NAVHistory.

    Covers equity / ETF / bond / foreign_equity — anything priced by a symbol.
    NSE-first, Yahoo fallback (see :func:`_fetch_equity_history`). Returns points
    written. A security with no symbol (e.g. an eCAS equity the ISIN database
    couldn't map to a ticker) is a no-op; the day-wise NAV table is shared with
    MFs, so equity closes flow into valuation through the same path."""
    if security.security_type not in _QUOTE_TYPES or not security.symbol:
        return 0
    history, source = _fetch_equity_history(
        security, since=since, nse_client=nse_client, yahoo_client=yahoo_client
    )
    existing = set(NAVHistory.objects.filter(security=security).values_list("date", flat=True))
    to_create = [
        NAVHistory(security=security, date=point.date, nav=point.nav, source=source)
        for point in history.points
        if point.date not in existing
    ]
    NAVHistory.objects.bulk_create(to_create)
    return len(to_create)


def _backfill_missing(qs, *, backfill_one, force: bool = False) -> dict:
    """Shared history-backfill loop for any feed (MF NAV or equity quote).

    For each security, skip when its stored series already reaches (within a
    trading-day grace of) the last trading day — otherwise re-pull from its
    earliest transaction and insert every missing date, so a series goes fully
    gap-free even if the app only ran intermittently. ``backfill_one`` is the
    per-security writer (:func:`backfill_nav_history` / :func:`backfill_equity_history`).

    ``force=True`` ignores the freshness skip and re-pulls every security — used
    to repair interior holes the freshness check (which only looks at the latest
    stored date) can't detect."""
    summary = {"securities": 0, "points": 0, "errors": 0, "skipped": 0, "closed": 0}
    today = timezone.localdate()
    cutoff = last_trading_day(today)
    fetched = False
    for security in qs:
        # Business-day aware so we don't refetch every weekend or for a not-yet-
        # published same-day price; gap aware so a fortnight-old series is brought
        # fully current, not just nudged.
        latest = NAVHistory.objects.filter(security=security).aggregate(m=Max("date"))["m"]
        behind = trading_days_between(latest, cutoff) if latest is not None else None
        if not force and behind is not None and behind <= _HISTORY_FRESH_TRADING_DAYS:
            summary["skipped"] += 1
            continue
        if fetched:
            _SLEEP(_REQUEST_SPACING)  # space consecutive live calls
        fetched = True
        # Cover the whole span the valuation series needs: the earliest of any
        # transaction date and any holding snapshot's as_of_date. A snapshot-only
        # equity (no transactions) must still backfill back to its statement date,
        # else the series is unpriced before it.
        txn_first = Transaction.objects.filter(security=security).aggregate(d=Min("date"))["d"]
        hold_first = Holding.objects.filter(security=security).aggregate(d=Min("as_of_date"))["d"]
        candidates = [d for d in (txn_first, hold_first) if d is not None]
        since = min(candidates) if candidates else None
        try:
            written = backfill_one(security, since=since)
        except (NAVFetchError, PriceFetchError) as exc:
            logger.warning(
                "backfill failed for security %s (%s) since=%s: %s",
                security.id,
                security.name,
                since,
                exc,
            )
            summary["errors"] += 1  # transient: leave it feed-pending, retry next cycle
            continue
        if written:
            summary["securities"] += 1
            summary["points"] += written
            if security.nav_feed_closed:  # data arrived → reopen a previously-dead code
                logger.info("security %s (%s): feed reopened", security.id, security.name)
                security.nav_feed_closed = False
                security.save(update_fields=["nav_feed_closed", "updated_at"])
        elif latest is None and not security.nav_feed_closed:
            # The feed responded (no error) with no history for a security we hold NO
            # price for: the code/ticker is dead (matured/delisted/unmappable), not
            # slow. Flag it so valuation degrades it instead of erroring + retrying
            # the feed forever.
            logger.warning(
                "security %s (%s): feed returned no history — marking closed",
                security.id,
                security.name,
            )
            security.nav_feed_closed = True
            security.save(update_fields=["nav_feed_closed", "updated_at"])
            summary["closed"] += 1
    return summary


def backfill_missing_history(
    *, securities: Iterable[Security] | None = None, force: bool = False
) -> dict:
    """Backfill MF NAV history, each fund bounded by its earliest transaction.

    See :func:`_backfill_missing` for the freshness / gap / ``force`` semantics
    (mfapi serves the full per-scheme series in one call)."""
    qs = (
        securities
        if securities is not None
        else Security.objects.filter(security_type=SecurityType.MF.value)
    )
    # One pooled mfapi connection for the whole batch (per-fund clients would
    # re-handshake TLS for every scheme).
    client = mfapi.shared_client()
    try:
        backfill_one = functools.partial(backfill_nav_history, client=client)
        return _backfill_missing(qs, backfill_one=backfill_one, force=force)
    finally:
        client.close()


def backfill_missing_equity_history(
    *, securities: Iterable[Security] | None = None, force: bool = False
) -> dict:
    """Backfill equity / ETF / bond price history via Yahoo, bounded by earliest
    transaction. Same freshness / gap / ``force`` semantics as the MF backfill —
    without this, quote-type holdings have at most a single latest point and so
    contribute nothing to the *historical* valuation series."""
    qs = (
        securities
        if securities is not None
        else Security.objects.filter(security_type__in=sorted(_QUOTE_TYPES))
    )
    # Warm one NSE session for the whole batch (the security-wise feed sits behind
    # a cookie wall) rather than re-warming per security, and pool one Yahoo
    # connection for the fallback.
    nse_client = nse_history.warmed_client()
    yahoo_client = yfinance_feed.shared_client()
    try:
        backfill_one = functools.partial(
            backfill_equity_history, nse_client=nse_client, yahoo_client=yahoo_client
        )
        return _backfill_missing(qs, backfill_one=backfill_one, force=force)
    finally:
        nse_client.close()
        yahoo_client.close()
