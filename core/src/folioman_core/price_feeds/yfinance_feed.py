"""Equity quotes + history via Yahoo Finance's public chart endpoint (no auth).

Yahoo's API is unofficial / scraped / rate-limited / occasionally restructured
— treat these as best-effort. Callers (``valuation.py`` / the equity backfill)
compose this with the NSE feeds and a stale-price state so a feed outage doesn't
take down the dashboard. For Indian equity *history* the NSE security-wise feed
(``nse_history``) is preferred; Yahoo is the fallback and the source for foreign
equities.

Symbol convention:
- Indian equity / ETF: ``SYMBOL.NS`` (NSE) or ``SYMBOL.BO`` (BSE).
- Foreign equity (US): plain ``SYMBOL`` (e.g. ``AAPL``).

Endpoints::

    GET /v8/finance/chart/{symbol}?interval=1d&range=1d                 # latest
    GET /v8/finance/chart/{symbol}?interval=1d&period1=..&period2=..    # history
    -> {"chart": {"result": [{"meta": {...}, "timestamp": [...],
                              "indicators": {"quote": [{"close": [...]}]}}],
                  "error": null}}
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from folioman_core.models.nav import NAVHistory, NAVPoint
from folioman_core.models.quote import Quote

BASE_URL = "https://query1.finance.yahoo.com"
DEFAULT_TIMEOUT = 15.0
# Yahoo blocks requests with empty / Python-default UA; mimic a real client.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15"
        " (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json",
}

_EXCHANGE_SUFFIX = {"NSE": ".NS", "BSE": ".BO"}

# Yahoo throttles aggressively (HTTP 429) with no Retry-After header, so the only
# recourse is blind exponential backoff. 5xx and transport errors get the same
# treatment; a 404 (unknown ticker) is permanent and fails immediately.
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # 1s, 2s, 4s
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_SLEEP = time.sleep


class PriceFetchError(RuntimeError):
    """Yahoo returned an error / unparseable response."""


def yahoo_symbol(symbol: str, exchange: str = "") -> str:
    """Construct the Yahoo ticker for an Indian symbol + exchange combo."""
    suffix = _EXCHANGE_SUFFIX.get(exchange.upper(), "")
    return f"{symbol.upper()}{suffix}" if suffix else symbol.upper()


def fetch_quote(
    symbol: str,
    *,
    exchange: str = "",
    client: httpx.Client | None = None,
) -> Quote | None:
    """Latest regular-market price for ``symbol`` on ``exchange``.

    Returns ``None`` when Yahoo has no data for the ticker (e.g. unlisted /
    delisted scrip). Raises ``PriceFetchError`` for transport / parse failures
    so the caller can decide whether to fall back to another feed or mark stale.
    """
    ticker = yahoo_symbol(symbol, exchange)
    payload = _fetch_json(f"/v8/finance/chart/{ticker}", client=client)
    err = (payload.get("chart") or {}).get("error")
    if err:
        msg = f"yahoo {ticker}: {err}"
        raise PriceFetchError(msg)
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        return None
    meta = results[0].get("meta") or {}
    raw_price = meta.get("regularMarketPrice")
    if raw_price is None:
        return None
    try:
        price = Decimal(str(raw_price))
    except (InvalidOperation, TypeError):
        msg = f"yahoo {ticker}: non-numeric regularMarketPrice {raw_price!r}"
        raise PriceFetchError(msg) from None
    epoch = meta.get("regularMarketTime")
    as_of = (
        datetime.fromtimestamp(epoch, tz=UTC).date()
        if isinstance(epoch, (int, float))
        else date.today()
    )
    return Quote(
        as_of=as_of,
        price=price,
        currency=meta.get("currency") or "INR",
        source="yfinance",
    )


def _epoch(d: date) -> int:
    """UTC-midnight epoch (seconds) for a date — Yahoo's period bounds."""
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def fetch_history(
    symbol: str,
    *,
    exchange: str = "",
    start: date | None = None,
    end: date | None = None,
    client: httpx.Client | None = None,
) -> NAVHistory:
    """Daily close-price history for ``symbol`` over ``[start, end]``, oldest-first.

    Mirrors :func:`mfapi.fetch_nav_history` (returns a ``NAVHistory`` of
    ``NAVPoint``) so the equity history backfill is symmetric with the MF one.
    Prices are the **unadjusted** daily close — the correct basis for marking a
    holding of a fixed share count to market on a past date. Returns an empty
    history when Yahoo has no data; raises ``PriceFetchError`` on transport /
    parse failure or an explicit Yahoo error (e.g. unknown ticker).
    """
    ticker = yahoo_symbol(symbol, exchange)
    params = ["interval=1d", "events=split,div"]
    if start is not None:
        upper = end or date.today()
        params.append(f"period1={_epoch(start)}")
        # period2 is an exclusive upper bound; pad a day so ``end`` is included.
        params.append(f"period2={_epoch(upper) + 86400}")
    else:
        params.append("range=max")
    payload = _fetch_json(f"/v8/finance/chart/{ticker}?{'&'.join(params)}", client=client)

    chart = payload.get("chart") or {}
    err = chart.get("error")
    if err:
        msg = f"yahoo {ticker}: {err}"
        raise PriceFetchError(msg)
    results = chart.get("result") or []
    if not results:
        return NAVHistory(points=[])
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    # Yahoo stamps each daily bar at the session time; shift by the exchange's
    # GMT offset so an NSE (IST) bar lands on its real trading day, not the UTC one.
    gmtoffset = (result.get("meta") or {}).get("gmtoffset") or 0

    points: list[NAVPoint] = []
    for ts, close in zip(timestamps, closes, strict=False):
        if ts is None or close is None:
            continue
        try:
            price = Decimal(str(close))
        except (InvalidOperation, TypeError):
            continue
        when = datetime.fromtimestamp(ts + gmtoffset, tz=UTC).date()
        if start is not None and when < start:
            continue
        if end is not None and when > end:
            continue
        points.append(NAVPoint(date=when, nav=price))
    points.sort(key=lambda p: p.date)
    return NAVHistory(points=points)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    # TimeoutException subclasses TransportError; both are worth a retry.
    return isinstance(exc, httpx.TransportError)


def _fetch_json(path: str, *, client: httpx.Client | None) -> dict:
    owned = client is None
    if owned:
        client = httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT, headers=_DEFAULT_HEADERS)
    try:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = client.get(path)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt < _MAX_RETRIES and _is_transient(exc):
                    _SLEEP(_BACKOFF_BASE * (2**attempt))
                    continue
                msg = f"yahoo GET {path} failed: {exc}"
                raise PriceFetchError(msg) from exc
        # Unreachable: the final attempt either returns or raises above.
        msg = f"yahoo GET {path}: exhausted retries"
        raise PriceFetchError(msg)
    finally:
        if owned:
            client.close()
