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
from math import ceil

from django.db import IntegrityError, transaction
from django.db.models import Max, Min
from django.utils import timezone
from folioman_core.models import SecurityType
from folioman_core.price_feeds import (
    amfi_bulk,
    captnemo,
    coingecko,
    mfapi,
    nse_bhavcopy,
    nse_history,
    yfinance_feed,
)
from folioman_core.price_feeds.errors import NAVFetchError
from folioman_core.price_feeds.yfinance_feed import PriceFetchError

from folioman_app.models import Holding, NAVHistory, Security, Transaction
from folioman_app.services.trading_calendar import (
    last_trading_day,
    trading_days,
    trading_days_between,
)

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
# The stored series counts as reaching the span start if its earliest date is within
# this of the first transaction/holding — the first trade may fall on a holiday a few
# days before the first available close, so an exact match isn't required.
_BACKFILL_TAIL_GRACE = timedelta(days=7)
# Window pulled to read an equity's *latest* close off the last row of its NSE
# security-wise history — wide enough to clear a long weekend / holiday run.
_QUOTE_LOOKBACK_DAYS = 10
# How many trading days back to look for the most recent published bhavcopy — today's
# isn't out until after the close, and a holiday run can sit a few days behind.
_BHAVCOPY_LOOKBACK = 5
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
        self._captnemo = None
        self._nse = None
        self._yahoo = None
        self._amfi = None

    @property
    def amfi(self):
        if self._amfi is None:
            self._amfi = amfi_bulk.shared_client()
        return self._amfi

    @property
    def mfapi(self):
        if self._mfapi is None:
            self._mfapi = mfapi.shared_client()
        return self._mfapi

    @property
    def captnemo(self):
        if self._captnemo is None:
            self._captnemo = captnemo.shared_client()
        return self._captnemo

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
        for client in (self._mfapi, self._captnemo, self._nse, self._yahoo, self._amfi):
            if client is not None:
                client.close()


def _fetch_mf_latest(security: Security, clients: _FeedClients):
    """Latest MF NAV: mfapi (by AMFI code) primary, captnemo (by ISIN) fallback.

    mfapi's ``/latest`` returns a single point, so it's the right tool for the
    daily pass; captnemo is a same-source ISIN-keyed mirror that covers an mfapi
    outage and prices an ISIN-only fund mfapi can't address. An mfapi *error* (not
    just empty) falls through to captnemo so a feed blip doesn't drop the day."""
    if security.amfi_code:
        try:
            point = mfapi.fetch_latest_nav(security.amfi_code, client=clients.mfapi)
            if point:
                return (point.date, point.nav, "mfapi")
        except NAVFetchError as exc:
            if not security.isin:
                raise
            logger.debug(
                "mfapi latest failed for %s (%s); trying captnemo: %s",
                security.id,
                security.name,
                exc,
            )
    if security.isin:
        point = captnemo.fetch_latest_nav(security.isin, client=clients.captnemo)
        if point:
            return (point.date, point.nav, "captnemo")
    return None


def _fetch_point(security: Security, clients: _FeedClients):
    """Return (date, nav, source) for the latest price, or None if unavailable."""
    stype = security.security_type
    if stype == SecurityType.MF.value:
        return _fetch_mf_latest(security, clients)
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


def _latest_fetch_day() -> date_cls:
    """Most recent trading day *before* today. NAVs/closes are never fetched for the
    current day (intraday, or not yet declared) — only completed trading sessions."""
    return last_trading_day(timezone.localdate() - timedelta(days=1))


def _prime_bulk(clients: _FeedClients) -> tuple[dict, dict]:
    """Fetch the day's whole-market snapshots once: AMFI NAVAll + NSE bhavcopy.

    Returns ``(mf_map, eq_map)`` of ``{id: (date, nav, source)}`` — MF keyed by
    AMFI code or ISIN, equity by NSE symbol. Either map is empty on a feed outage,
    so :func:`refresh_navs` transparently falls back to a per-security fetch. This
    is the whole point: one request for the entire MF universe and one for the
    entire cash market, instead of one per security every day.
    """
    mf_map: dict[str, tuple] = {}
    eq_map: dict[str, tuple] = {}
    try:
        mf_map = {
            key: (p.date, p.nav, "amfi")
            for key, p in amfi_bulk.fetch_all_latest(client=clients.amfi).items()
        }
    except NAVFetchError as exc:
        logger.warning("AMFI bulk NAV unavailable — falling back per-scheme: %s", exc)
    try:
        # Its own warmed session (not clients.nse, which the per-symbol fallback
        # owns): on the happy path the fallback never runs, so this is the pass's
        # only NSE warm-up. Start from the last completed trading day (never today),
        # stepping further back over any unmodelled holiday until a bhavcopy exists.
        bhav = nse_bhavcopy.warmed_client()
        try:
            day = _latest_fetch_day()
            for _ in range(_BHAVCOPY_LOOKBACK):
                closes = nse_bhavcopy.fetch_close_by_symbol(day, client=bhav)
                if closes:
                    eq_map = {sym: (p.date, p.nav, "nse-bhavcopy") for sym, p in closes.items()}
                    break
                day = last_trading_day(day - timedelta(days=1))
        finally:
            bhav.close()
    except Exception as exc:  # best-effort: any bhavcopy failure → per-symbol quotes
        logger.warning("NSE bhavcopy unavailable — falling back per-symbol: %s", exc)
    return mf_map, eq_map


def _bulk_point(security: Security, mf_map: dict, eq_map: dict):
    """Today's (date, nav, source) from the pre-fetched bulk maps, or None."""
    stype = security.security_type
    if stype == SecurityType.MF.value:
        for key in (security.amfi_code, security.isin):
            if key and key in mf_map:
                return mf_map[key]
    elif (
        stype in _QUOTE_TYPES
        and stype != SecurityType.FOREIGN_EQUITY.value
        and security.symbol
        and security.exchange in ("", "NSE")
    ):
        return eq_map.get(security.symbol.upper())
    return None


def refresh_navs(*, securities: Iterable[Security] | None = None) -> dict:
    qs = Security.objects.all() if securities is None else securities
    summary = {"updated": 0, "skipped": 0, "errors": 0}
    clients = _FeedClients()
    live_fetched = False
    try:
        mf_map, eq_map = _prime_bulk(clients)
        for security in qs:
            point = _bulk_point(security, mf_map, eq_map)
            if point is None:
                # No bulk hit (foreign equity, crypto, a delisted symbol, or a bulk
                # outage) → fetch this one live, spacing only the live calls.
                if live_fetched:
                    _SLEEP(_REQUEST_SPACING)
                live_fetched = True
                try:
                    point = _fetch_point(security, clients)
                except (NAVFetchError, PriceFetchError) as exc:
                    logger.warning(
                        "NAV refresh failed for security %s (%s): %s",
                        security.id,
                        security.name,
                        exc,
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


def _fetch_mf_history(security, *, since, mfapi_client, captnemo_client):
    """Full MF NAV series: captnemo (by ISIN) primary, mfapi (by AMFI) fallback.

    Backfill pulls the whole series in one call, so captnemo leads — it serves the
    full history always (no /latest), is edge-cached and fast, and is oldest-first
    already. mfapi backstops a captnemo outage and covers a fund we only know by
    AMFI code. When mfapi backfills a fund whose ISIN we don't yet store, its meta
    carries one — persist it so captnemo can lead next time.

    Returns ``(history, source)`` or ``None`` when the fund has no usable id."""
    if security.isin:
        try:
            history = captnemo.fetch_nav_history(security.isin, since=since, client=captnemo_client)
            return history, "captnemo"
        except NAVFetchError as exc:
            if not security.amfi_code:
                raise
            logger.debug(
                "captnemo backfill failed for %s (%s); trying mfapi: %s",
                security.id,
                security.name,
                exc,
            )
    if security.amfi_code:
        history = mfapi.fetch_nav_history(security.amfi_code, since=since, client=mfapi_client)
        if history.isin and not security.isin:
            security.isin = history.isin
            try:
                with transaction.atomic():
                    security.save(update_fields=["isin", "updated_at"])
            except IntegrityError:
                # The ISIN is already claimed by another security row — leave this
                # one AMFI-keyed rather than failing the backfill. Not fatal: mfapi
                # still served the history we're about to write.
                security.isin = ""
                logger.warning(
                    "security %s (%s): ISIN %s already in use; staying AMFI-keyed",
                    security.id,
                    security.name,
                    history.isin,
                )
        return history, "mfapi"
    return None


def backfill_nav_history(
    security: Security,
    *,
    since: date_cls | None = None,
    mfapi_client=None,
    captnemo_client=None,
) -> int:
    """Backfill an MF security's NAV history into NAVHistory. Returns points written.

    captnemo (ISIN) leads, mfapi (AMFI code) backstops — see :func:`_fetch_mf_history`.
    The ``*_client`` args let a batch reuse one pooled connection per feed; when
    ``None`` each feed opens (and closes) its own."""
    if security.security_type != SecurityType.MF.value:
        return 0
    fetched = _fetch_mf_history(
        security, since=since, mfapi_client=mfapi_client, captnemo_client=captnemo_client
    )
    if fetched is None:
        return 0
    history, source = fetched
    existing = set(NAVHistory.objects.filter(security=security).values_list("date", flat=True))
    to_create = [
        NAVHistory(security=security, date=point.date, nav=point.nav, source=source)
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


# NSE security-wise history is fetched in ≤1-year chunks (see nse_history), so a
# per-symbol backfill costs that many requests — the yardstick the bulk switch beats.
_NSE_CHUNK_DAYS = 365


def _backfill_candidate(security: Security, cutoff: date_cls, force: bool):
    """``(since, latest)`` if ``security`` needs a history backfill, else ``None``.

    ``since`` is the earliest date the valuation series must reach — the earliest
    of any transaction date and any holding snapshot's as_of_date (a snapshot-only
    equity must still price back to its statement date). Skips only when the stored
    series is current at the head AND reaches that span start; a head-only check let
    a shallow series look "fresh" forever, leaving a freshly imported holding's first
    trade unpriced, so the tail check self-heals it.
    """
    txn_first = Transaction.objects.filter(security=security).aggregate(d=Min("date"))["d"]
    hold_first = Holding.objects.filter(security=security).aggregate(d=Min("as_of_date"))["d"]
    candidates = [d for d in (txn_first, hold_first) if d is not None]
    since = min(candidates) if candidates else None
    bounds = NAVHistory.objects.filter(security=security).aggregate(lo=Min("date"), hi=Max("date"))
    latest, earliest = bounds["hi"], bounds["lo"]
    behind = trading_days_between(latest, cutoff) if latest is not None else None
    reaches_back = since is None or (
        earliest is not None and earliest <= since + _BACKFILL_TAIL_GRACE
    )
    if not force and behind is not None and behind <= _HISTORY_FRESH_TRADING_DAYS and reaches_back:
        return None
    return since, latest


def _run_backfill_one(security, since, latest, backfill_one, summary) -> None:
    """Per-security backfill via ``backfill_one``, updating ``summary`` and the
    feed-closed flag (reopen on data, close a code that responds with no history)."""
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
        return
    if written:
        summary["securities"] += 1
        summary["points"] += written
        if security.nav_feed_closed:  # data arrived → reopen a previously-dead code
            logger.info("security %s (%s): feed reopened", security.id, security.name)
            security.nav_feed_closed = False
            security.save(update_fields=["nav_feed_closed", "updated_at"])
    elif latest is None and not security.nav_feed_closed:
        # The feed responded (no error) with no history for a security we hold NO
        # price for: the code/ticker is dead (matured/delisted/unmappable), not slow.
        # Flag it so valuation degrades it instead of erroring + retrying forever.
        logger.warning(
            "security %s (%s): feed returned no history — marking closed",
            security.id,
            security.name,
        )
        security.nav_feed_closed = True
        security.save(update_fields=["nav_feed_closed", "updated_at"])
        summary["closed"] += 1


def _bhavcopy_eligible(security: Security) -> bool:
    """Whether an equity can be priced from the NSE bhavcopy (NSE-listed, symboled)."""
    return bool(
        security.symbol
        and security.security_type != SecurityType.FOREIGN_EQUITY.value
        and security.exchange in ("", "NSE")
    )


def _prefer_bulk(candidates: list[tuple], cutoff: date_cls) -> bool:
    """Bulk (one bhavcopy per trading day over the span) vs per-symbol history.

    Bulk cost is the number of trading days in ``[min_since, cutoff]`` — one file
    covers *every* symbol that day. Per-symbol cost is the sum of ≤1-year history
    chunks each symbol needs. Bulk wins for a shallow span across many symbols (the
    intermittent-catch-up case); per-symbol wins when even one symbol needs deep
    history (its old ``since`` blows up the span).
    """
    min_since = min(since for _, since, _ in candidates)
    bulk_cost = trading_days_between(min_since - timedelta(days=1), cutoff)
    per_symbol_cost = sum(
        max(1, ceil((cutoff - since).days / _NSE_CHUNK_DAYS)) for _, since, _ in candidates
    )
    return 0 < bulk_cost < per_symbol_cost


def _bulk_backfill_equity(candidates: list[tuple], cutoff: date_cls, summary: dict) -> set[int]:
    """Backfill NSE-listed ``candidates`` from the daily bhavcopy: fetch each trading
    day in the (shallow) span once and scatter its closes across every symbol that
    needs it. Returns the security ids it covered (symbol seen in a bhavcopy)."""
    start = min(since for _, since, _ in candidates)
    state: dict[int, list] = {}
    by_symbol: dict[str, list[int]] = {}
    for security, since, _latest in candidates:
        existing = set(NAVHistory.objects.filter(security=security).values_list("date", flat=True))
        state[security.id] = [security, since, existing]
        by_symbol.setdefault(security.symbol.upper(), []).append(security.id)

    handled: set[int] = set()
    written_ids: set[int] = set()
    to_create: list[NAVHistory] = []
    client = nse_bhavcopy.warmed_client()
    fetched = False
    try:
        # Never fetch today's (incomplete) session — cap at the last completed day.
        for day in trading_days(start, min(cutoff, _latest_fetch_day())):
            if fetched:
                _SLEEP(_REQUEST_SPACING)
            fetched = True
            for symbol, point in nse_bhavcopy.fetch_close_by_symbol(day, client=client).items():
                for sec_id in by_symbol.get(symbol, ()):
                    handled.add(sec_id)  # symbol trades → this security is bhavcopy-covered
                    security, since, existing = state[sec_id]
                    if day >= since and day not in existing:
                        to_create.append(
                            NAVHistory(
                                security=security, date=day, nav=point.nav, source="nse-bhavcopy"
                            )
                        )
                        existing.add(day)
                        written_ids.add(sec_id)
    finally:
        client.close()

    NAVHistory.objects.bulk_create(to_create)
    summary["securities"] += len(written_ids)
    summary["points"] += len(to_create)
    for sec_id in written_ids:  # data arrived → reopen a previously-dead code
        security = state[sec_id][0]
        if security.nav_feed_closed:
            security.nav_feed_closed = False
            security.save(update_fields=["nav_feed_closed", "updated_at"])
    return handled


def _backfill_missing(qs, *, backfill_one, force: bool = False, bulk_backfill=None) -> dict:
    """Shared history-backfill loop for any feed (MF NAV or equity quote).

    Collects the securities that need a backfill (see :func:`_backfill_candidate`),
    then — when ``bulk_backfill`` is supplied (equity) and a whole-market snapshot is
    cheaper than per-symbol pulls — routes the eligible ones through it, per-symbol
    for the rest. MF passes no ``bulk_backfill``, so it stays per-scheme (one mfapi
    call already serves a fund's full history).

    ``force=True`` ignores the freshness skip and re-pulls every security — to repair
    interior holes the head-only freshness check can't detect. Bulk is skipped under
    ``force`` (a forced deep re-pull is exactly the per-symbol-favourable case)."""
    summary = {"securities": 0, "points": 0, "errors": 0, "skipped": 0, "closed": 0}
    cutoff = last_trading_day(timezone.localdate())
    needing: list[tuple] = []
    for security in qs:
        got = _backfill_candidate(security, cutoff, force)
        if got is None:
            summary["skipped"] += 1
            continue
        needing.append((security, got[0], got[1]))
    if not needing:
        return summary

    if bulk_backfill is not None and not force:
        eligible = [c for c in needing if c[1] is not None and _bhavcopy_eligible(c[0])]
        if eligible and _prefer_bulk(eligible, cutoff):
            logger.info(
                "equity backfill: %d symbols over a shallow span → bhavcopy bulk "
                "(cheaper than per-symbol history)",
                len(eligible),
            )
            handled = bulk_backfill(eligible, cutoff, summary)
            needing = [c for c in needing if c[0].id not in handled]

    fetched = False
    for security, since, latest in needing:
        if fetched:
            _SLEEP(_REQUEST_SPACING)  # space consecutive live calls
        fetched = True
        _run_backfill_one(security, since, latest, backfill_one, summary)
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
    # One pooled connection per feed for the whole batch (per-fund clients would
    # re-handshake TLS for every scheme). captnemo leads, mfapi backstops.
    mfapi_client = mfapi.shared_client()
    captnemo_client = captnemo.shared_client()
    try:
        backfill_one = functools.partial(
            backfill_nav_history, mfapi_client=mfapi_client, captnemo_client=captnemo_client
        )
        return _backfill_missing(qs, backfill_one=backfill_one, force=force)
    finally:
        mfapi_client.close()
        captnemo_client.close()


def backfill_missing_equity_history(
    *, securities: Iterable[Security] | None = None, force: bool = False
) -> dict:
    """Backfill equity / ETF / bond price history, bounded by earliest transaction.

    Same freshness / gap / ``force`` semantics as the MF backfill — without this,
    quote-type holdings have at most a single latest point and so contribute nothing
    to the *historical* valuation series. When many symbols each need only a shallow
    catch-up, one bhavcopy per day is cheaper than per-symbol history, so the loop
    routes them through :func:`_bulk_backfill_equity`; per-symbol (NSE-first, Yahoo
    fallback) covers deep history and non-NSE names."""
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
        return _backfill_missing(
            qs, backfill_one=backfill_one, force=force, bulk_backfill=_bulk_backfill_equity
        )
    finally:
        nse_client.close()
        yahoo_client.close()
