"""Crypto spot price via CoinGecko's free Simple Price endpoint.

No auth required; rate-limited (~30 calls/min free tier). Fetched directly in
INR so no FX conversion is needed downstream.

Endpoint::

    GET /api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=inr
    -> {"bitcoin": {"inr": 8500000.0}, "ethereum": {"inr": 250000.0}}

Coin IDs come from CoinGecko (e.g. ``"bitcoin"``, ``"ethereum"``); folioman
expects the user to set ``Security.metadata['coin_id']`` at security creation.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from folioman_core.models.quote import Quote
from folioman_core.price_feeds.yfinance_feed import PriceFetchError

BASE_URL = "https://api.coingecko.com"
DEFAULT_TIMEOUT = 15.0


def fetch_quote(
    coin_id: str,
    *,
    vs_currency: str = "INR",
    client: httpx.Client | None = None,
) -> Quote | None:
    """Spot price for a single CoinGecko coin id, or ``None`` if unknown."""
    quotes = fetch_quotes([coin_id], vs_currency=vs_currency, client=client)
    return quotes.get(coin_id.lower())


def fetch_quotes(
    coin_ids: Iterable[str],
    *,
    vs_currency: str = "INR",
    client: httpx.Client | None = None,
) -> dict[str, Quote]:
    """Batched fetch — one HTTP call for many coins; the free tier favours this."""
    ids = sorted({c.lower().strip() for c in coin_ids if c and c.strip()})
    if not ids:
        return {}
    payload = _fetch_json(
        "/api/v3/simple/price",
        params={"ids": ",".join(ids), "vs_currencies": vs_currency.lower()},
        client=client,
    )
    quotes: dict[str, Quote] = {}
    today = date.today()
    for coin_id in ids:
        entry = payload.get(coin_id)
        if not entry:
            continue
        raw = entry.get(vs_currency.lower())
        if raw is None:
            continue
        try:
            price = Decimal(str(raw))
        except (InvalidOperation, TypeError):
            continue
        quotes[coin_id] = Quote(
            as_of=today, price=price, currency=vs_currency.upper(), source="coingecko"
        )
    return quotes


def _fetch_json(path: str, *, params: dict, client: httpx.Client | None) -> dict:
    owned = client is None
    if owned:
        client = httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT)
    try:
        response = client.get(path, params=params)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        msg = f"coingecko GET {path} failed: {exc}"
        raise PriceFetchError(msg) from exc
    finally:
        if owned:
            client.close()
