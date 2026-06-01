"""NSE / BSE direct quote feed — alternate when yfinance is unreachable.

The NSE public API (``/api/quote-equity``) is notoriously hard to call from a
script: it requires a session cookie obtained from a prior page load and rejects
requests with empty / non-browser ``User-Agent``. We try a single best-effort
GET; anything other than a 200 with the expected JSON shape returns ``None``
so callers can fall through to a stale-price state.

The plan parks a robust implementation (cookie warmup, retries) for v1.5; this
session ships the seam + a thin attempt so the fallback chain is wired.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from folioman_core.models.quote import Quote
from folioman_core.price_feeds.yfinance_feed import PriceFetchError

# NSE expects a Referer header pointing at a real page.
_NSE_BASE = "https://www.nseindia.com"
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15"
        " (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json",
    "Referer": f"{_NSE_BASE}/get-quotes/equity",
}
DEFAULT_TIMEOUT = 10.0


def fetch_quote(
    symbol: str,
    *,
    client: httpx.Client | None = None,
) -> Quote | None:
    """Best-effort latest NSE quote for ``symbol``.

    Returns ``None`` on any failure (cookie wall, HTTP error, parse error) so
    that the higher-level fallback chain can surface a stale-price state without
    raising. Real exceptions are *not* propagated — this is explicitly a
    fallback, and propagating noise would defeat the resilience intent.
    """
    owned = client is None
    if owned:
        client = httpx.Client(base_url=_NSE_BASE, timeout=DEFAULT_TIMEOUT, headers=_NSE_HEADERS)
    try:
        try:
            response = client.get(f"/api/quote-equity?symbol={symbol.upper()}")
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        price_info = payload.get("priceInfo") or {}
        raw_price = price_info.get("lastPrice")
        if raw_price is None:
            return None
        try:
            price = Decimal(str(raw_price))
        except (InvalidOperation, TypeError):
            return None
        return Quote(as_of=date.today(), price=price, currency="INR", source="nse")
    finally:
        if owned:
            client.close()


# Re-export so callers composing the fallback chain can catch PriceFetchError
# from either feed via a single import.
__all__ = ["PriceFetchError", "fetch_quote"]
