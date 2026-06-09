"""NSE / BSE direct quote feed — the NSE-first leg of the latest-price chain.

The NSE public API (``/api/quote-equity``) requires a session cookie obtained
from a prior page load: called cold it answers **403**, which would push every
quote onto the rate-limited Yahoo fallback. So we prime the session first with a
GET to the ``get-quotes`` page (following its redirect to collect cookies), then
call the API — the same warmup the history feed uses. Any non-200/parse failure
still returns ``None`` so callers fall through to a stale-price state.

A caller doing a batch can warm one client and pass it in (see ``warmed_client``);
a lone call warms its own.
"""

from __future__ import annotations

import contextlib
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
# Page whose load hands out the anti-bot session cookie the API call needs.
_WARMUP_PATH = "/get-quotes/equity"


def warmed_client() -> httpx.Client:
    """An httpx client with NSE session cookies primed, for batching quotes across
    symbols on one warmed session. A failed warmup is non-fatal — the quote call
    still attempts, and any cookies it sets persist."""
    client = httpx.Client(
        base_url=_NSE_BASE, headers=_NSE_HEADERS, timeout=DEFAULT_TIMEOUT, follow_redirects=True
    )
    with contextlib.suppress(httpx.HTTPError):
        client.get(_WARMUP_PATH, params={"symbol": "RELIANCE"})
    return client


def fetch_quote(
    symbol: str,
    *,
    client: httpx.Client | None = None,
) -> Quote | None:
    """Best-effort latest NSE quote for ``symbol``.

    Returns ``None`` on any failure (cookie wall, HTTP error, parse error) so
    that the higher-level fallback chain can surface a stale-price state without
    raising. Real exceptions are *not* propagated — this is explicitly a
    fallback, and propagating noise would defeat the resilience intent. A lone
    call warms its own session; a passed-in ``client`` is assumed already warmed.
    """
    owned = client is None
    if owned:
        client = warmed_client()
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
