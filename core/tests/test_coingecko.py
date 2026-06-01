"""CoinGecko crypto-price wrapper. HTTP mocked."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from folioman_core.price_feeds import coingecko
from folioman_core.price_feeds.yfinance_feed import PriceFetchError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=coingecko.BASE_URL)


def test_fetch_quote_single_coin_inr():
    def handler(request):
        assert request.url.path == "/api/v3/simple/price"
        # ids are sorted + lowercased before dispatch
        assert request.url.params["ids"] == "bitcoin"
        assert request.url.params["vs_currencies"] == "inr"
        return httpx.Response(200, json={"bitcoin": {"inr": 8500000.50}})

    with _client(handler) as c:
        quote = coingecko.fetch_quote("Bitcoin", client=c)

    assert quote is not None
    assert quote.price == Decimal("8500000.50")
    assert quote.currency == "INR"
    assert quote.source == "coingecko"
    assert quote.as_of == date.today()


def test_fetch_quotes_batched_and_sorted():
    def handler(request):
        assert request.url.params["ids"] == "bitcoin,ethereum"  # sorted
        return httpx.Response(
            200,
            json={"bitcoin": {"inr": 8500000}, "ethereum": {"inr": 250000.75}},
        )

    with _client(handler) as c:
        quotes = coingecko.fetch_quotes(["Ethereum", "BITCOIN"], client=c)

    assert set(quotes) == {"bitcoin", "ethereum"}
    assert quotes["bitcoin"].price == Decimal("8500000")
    assert quotes["ethereum"].price == Decimal("250000.75")


def test_fetch_quotes_skips_unknown_coin():
    def handler(_request):
        return httpx.Response(200, json={"bitcoin": {"inr": 8500000}})  # ethereum omitted

    with _client(handler) as c:
        quotes = coingecko.fetch_quotes(["bitcoin", "unknown-coin"], client=c)

    assert set(quotes) == {"bitcoin"}


def test_fetch_quote_returns_none_when_coin_missing():
    def handler(_request):
        return httpx.Response(200, json={})

    with _client(handler) as c:
        assert coingecko.fetch_quote("not-a-coin", client=c) is None


def test_fetch_quotes_empty_input_no_http_call():
    def handler(_request):  # pragma: no cover — must not be invoked
        raise AssertionError("HTTP call should not happen for empty input")

    with _client(handler) as c:
        assert coingecko.fetch_quotes([], client=c) == {}


def test_fetch_quote_http_500_raises():
    def handler(_request):
        return httpx.Response(500, text="rate limited")

    with _client(handler) as c, pytest.raises(PriceFetchError):
        coingecko.fetch_quote("bitcoin", client=c)


def test_fetch_quote_non_numeric_price_skipped():
    def handler(_request):
        return httpx.Response(200, json={"bitcoin": {"inr": "not_a_decimal"}})

    with _client(handler) as c:
        assert coingecko.fetch_quote("bitcoin", client=c) is None
