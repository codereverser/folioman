"""NSE security-wise historical price feed — per-symbol daily close.

Primary Indian-equity history source for the backfill: it returns NSE's own
closing price (authoritative, matches bhavcopy) and is a *different provider*
than Yahoo, which rate-limits aggressively. Same ``NAVHistory`` return shape as
``mfapi.fetch_nav_history`` / ``yfinance_feed.fetch_history`` so it slots into the
same fallback chain.

The endpoint sits behind NSE's cookie wall — a plain request is rejected until a
browser-like session cookie is obtained from a prior page load. We warm one with
a GET to a real NSE page, then call the security-archives **CSV export** (the
JSON variant caps each response near ~3 months / ~70 rows; the CSV download does
not, returning the full requested range). The CSV itself fails above ~2 years per
request, so a wider window is still fetched in ~1-year chunks::

    GET /api/historicalOR/generateSecurityWiseHistoricalData
        ?from=DD-MM-YYYY&to=DD-MM-YYYY&symbol=SYM
        &type=priceVolumeDeliverable&series=EQ&csv=true
    -> CSV: "Symbol","Series","Date","Prev Close",...,"Close Price",...
            "RELIANCE","EQ","08-Jun-2026",...,"1,263.30",...

Best-effort: a chunk that doesn't come back as CSV (NSE serves an HTML error /
WAF page on overload) is skipped; if *no* chunk yields data the call raises
``PriceFetchError`` so the caller can fall back to Yahoo or retry next tick.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from folioman_core.models.nav import NAVHistory, NAVPoint
from folioman_core.price_feeds.nse_bse_client import NSE_BASE_URL, ExchangeClient, nse_client
from folioman_core.price_feeds.yfinance_feed import PriceFetchError

_HISTORY_PATH = "/api/historicalOR/generateSecurityWiseHistoricalData"
_REFERER = f"{NSE_BASE_URL}/report-detail/eq_security"
# The CSV export returns the full requested range (unlike the ~3-month JSON cap),
# but errors out above ~2 years per request — chunk safely under that.
_MAX_WINDOW_DAYS = 365
_CHUNK_SPACING = 0.2  # polite gap between chunk requests for one symbol
_SLEEP = time.sleep


def warmed_client() -> ExchangeClient:
    """Return an NSE client with session cookies primed (shared NSE/BSE client).

    A wide backfill reuses one warmed client across symbols rather than warming
    per call. A failed warmup is non-fatal — the history call still attempts, and
    any cookies it does set persist on the client.
    """
    return nse_client()


def _windows(start: date, end: date):
    """Yield ``(from, to)`` sub-ranges no wider than the per-request CSV cap."""
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=_MAX_WINDOW_DAYS - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def _parse_csv(text: str) -> list[NAVPoint]:
    """Parse the security-wise CSV into ``NAVPoint``s (Date + Close Price).

    Header cells carry a UTF-8 BOM and trailing spaces (``"Date  "``); values are
    quoted and use Indian comma grouping (``"1,263.30"``). Rows missing a parseable
    date / close are skipped.
    """
    points: list[NAVPoint] = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    for raw in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        ts = row.get("Date", "")
        close = row.get("Close Price", "").replace(",", "")
        if not ts or not close:
            continue
        try:
            when = datetime.strptime(ts, "%d-%b-%Y").date()
            price = Decimal(close)
        except (ValueError, InvalidOperation):
            continue
        points.append(NAVPoint(date=when, nav=price))
    return points


def fetch_history(
    symbol: str,
    *,
    start: date | None = None,
    end: date | None = None,
    client: ExchangeClient | None = None,
) -> NAVHistory:
    """Daily close history for an NSE ``symbol`` over ``[start, end]``, oldest-first.

    ``client`` lets a batch reuse one warmed session; when omitted, a warmed
    client is created and closed for this call. Raises ``PriceFetchError`` only
    when no chunk yielded data (so the caller can fall back to another feed); a
    partial result from some chunks succeeding is returned as-is.
    """
    if not symbol:
        return NAVHistory(points=[])
    end = end or date.today()
    start = start or (end - timedelta(days=_MAX_WINDOW_DAYS))

    owned = client is None
    if owned:
        client = warmed_client()
    points: list[NAVPoint] = []
    last_error: Exception | None = None
    try:
        for i, (w_start, w_end) in enumerate(_windows(start, end)):
            if i:
                _SLEEP(_CHUNK_SPACING)
            params = {
                "from": w_start.strftime("%d-%m-%Y"),
                "to": w_end.strftime("%d-%m-%Y"),
                "symbol": symbol.upper(),
                "type": "priceVolumeDeliverable",
                "series": "EQ",
                "csv": "true",
            }
            try:
                response = client.get(_HISTORY_PATH, params=params, headers={"Referer": _REFERER})
                response.raise_for_status()
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            # NSE serves an HTML error / WAF page (not CSV) when a request is too
            # wide or it's throttling — skip that chunk rather than parse garbage.
            if "text/csv" not in response.headers.get("content-type", ""):
                last_error = PriceFetchError(f"nse history {symbol}: non-CSV response")
                continue
            points.extend(_parse_csv(response.text))
    finally:
        if owned:
            client.close()

    if not points and last_error is not None:
        msg = f"nse history {symbol}: {last_error}"
        raise PriceFetchError(msg) from last_error

    # Sort oldest-first, drop dupes at chunk seams, clamp to the window.
    points.sort(key=lambda p: p.date)
    seen: set[date] = set()
    deduped: list[NAVPoint] = []
    for p in points:
        if p.date in seen or not (start <= p.date <= end):
            continue
        seen.add(p.date)
        deduped.append(p)
    return NAVHistory(points=deduped)
