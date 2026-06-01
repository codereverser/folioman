"""Yahoo Finance chart-endpoint wrapper. HTTP mocked."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from folioman_core.price_feeds import yfinance_feed
from folioman_core.price_feeds.yfinance_feed import PriceFetchError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=yfinance_feed.BASE_URL)


def _chart(price: float, *, currency: str = "INR", epoch: int | None = 1735603200) -> dict:
    meta = {"regularMarketPrice": price, "currency": currency, "regularMarketTime": epoch}
    return {"chart": {"result": [{"meta": meta}], "error": None}}


def test_yahoo_symbol_appends_exchange_suffix():
    assert yfinance_feed.yahoo_symbol("reliance", "NSE") == "RELIANCE.NS"
    assert yfinance_feed.yahoo_symbol("RELIANCE", "BSE") == "RELIANCE.BO"
    assert yfinance_feed.yahoo_symbol("AAPL", "") == "AAPL"


def test_fetch_quote_indian_equity_inr():
    def handler(request):
        assert request.url.path == "/v8/finance/chart/RELIANCE.NS"
        return httpx.Response(200, json=_chart(2850.50))

    with _client(handler) as c:
        quote = yfinance_feed.fetch_quote("RELIANCE", exchange="NSE", client=c)

    assert quote is not None
    assert quote.price == Decimal("2850.5")
    assert quote.currency == "INR"
    assert quote.source == "yfinance"
    assert quote.as_of == date(2024, 12, 31)  # 1735603200 = 2024-12-31 UTC


def test_fetch_quote_foreign_equity_carries_native_currency():
    def handler(request):
        assert request.url.path == "/v8/finance/chart/AAPL"
        return httpx.Response(200, json=_chart(180.25, currency="USD"))

    with _client(handler) as c:
        quote = yfinance_feed.fetch_quote("AAPL", client=c)

    assert quote.currency == "USD"
    assert quote.price == Decimal("180.25")


def test_fetch_quote_returns_none_when_chart_empty():
    def handler(_request):
        return httpx.Response(200, json={"chart": {"result": [], "error": None}})

    with _client(handler) as c:
        assert yfinance_feed.fetch_quote("ZZZNONE", exchange="NSE", client=c) is None


def test_fetch_quote_yahoo_error_field_raises():
    def handler(_request):
        err = {"code": "Not Found", "description": "no data"}
        return httpx.Response(200, json={"chart": {"result": None, "error": err}})

    with _client(handler) as c, pytest.raises(PriceFetchError, match="Not Found"):
        yfinance_feed.fetch_quote("BADSYM", exchange="NSE", client=c)


def test_fetch_quote_http_500_raises():
    def handler(_request):
        return httpx.Response(500, text="oops")

    with _client(handler) as c, pytest.raises(PriceFetchError):
        yfinance_feed.fetch_quote("RELIANCE", exchange="NSE", client=c)


def test_fetch_quote_non_numeric_price_raises():
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "chart": {
                    "result": [{"meta": {"regularMarketPrice": "not_a_number", "currency": "INR"}}],
                    "error": None,
                }
            },
        )

    with _client(handler) as c, pytest.raises(PriceFetchError, match="non-numeric"):
        yfinance_feed.fetch_quote("X", exchange="NSE", client=c)


def test_fetch_quote_missing_market_time_falls_back_to_today():
    def handler(_request):
        return httpx.Response(200, json=_chart(100.0, epoch=None))

    with _client(handler) as c:
        quote = yfinance_feed.fetch_quote("X", exchange="NSE", client=c)
    assert quote.as_of == date.today()
