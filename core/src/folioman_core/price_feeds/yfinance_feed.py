"""Equity quotes via Yahoo Finance's public chart endpoint (no auth).

Yahoo's API is unofficial / scraped / rate-limited / occasionally restructured
— treat ``fetch_quote`` as best-effort. Callers (``valuation.py``) compose this
with the NSE/BSE fallback and a stale-price state so a feed outage doesn't
take down the dashboard.

Symbol convention:
- Indian equity / ETF: ``SYMBOL.NS`` (NSE) or ``SYMBOL.BO`` (BSE).
- Foreign equity (US): plain ``SYMBOL`` (e.g. ``AAPL``).

Endpoint::

    GET /v8/finance/chart/{symbol}?interval=1d&range=1d
    -> {"chart": {"result": [{"meta": {"regularMarketPrice": ...,
                                       "currency": ...}}],
                  "error": null}}
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

import httpx

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


def _fetch_json(path: str, *, client: httpx.Client | None) -> dict:
    owned = client is None
    if owned:
        client = httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT, headers=_DEFAULT_HEADERS)
    try:
        response = client.get(path)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        msg = f"yahoo GET {path} failed: {exc}"
        raise PriceFetchError(msg) from exc
    finally:
        if owned:
            client.close()
