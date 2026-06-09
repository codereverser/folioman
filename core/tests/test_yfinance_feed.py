"""Yahoo Finance chart-endpoint wrapper. HTTP mocked."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from folioman_core.price_feeds import yfinance_feed
from folioman_core.price_feeds.yfinance_feed import PriceFetchError


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Don't actually sleep on retry backoff during tests."""
    monkeypatch.setattr(yfinance_feed, "_SLEEP", lambda _s: None)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=yfinance_feed.BASE_URL)


def _history_chart(
    rows: list[tuple[int, float | None]], *, gmtoffset: int = 19800, error=None
) -> dict:
    """Build a /v8/finance/chart range payload from (epoch, close) rows."""
    return {
        "chart": {
            "error": error,
            "result": [
                {
                    "meta": {"gmtoffset": gmtoffset, "currency": "INR"},
                    "timestamp": [ts for ts, _ in rows],
                    "indicators": {"quote": [{"close": [c for _, c in rows]}]},
                }
            ],
        }
    }


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


# 1746498600 = 2025-05-06, 1746585000 = 2025-05-07, 1746671400 = 2025-05-08 (IST bars)
def test_fetch_history_parses_oldest_first_decimals_and_skips_nulls():
    captured = {}

    def handler(request):
        captured["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=_history_chart([(1746671400, 1425.25), (1746498600, 1410.5), (1746585000, None)]),
        )

    with _client(handler) as c:
        hist = yfinance_feed.fetch_history(
            "RELIANCE", exchange="NSE", start=date(2025, 5, 1), end=date(2025, 5, 31), client=c
        )
    assert captured["query"]["interval"] == "1d"
    assert "events" in captured["query"]
    # Null close dropped; oldest-first IST dates; Decimal prices.
    assert [(p.date, p.nav) for p in hist.points] == [
        (date(2025, 5, 6), Decimal("1410.5")),
        (date(2025, 5, 8), Decimal("1425.25")),
    ]


def test_fetch_history_empty_result_returns_empty():
    def handler(_request):
        return httpx.Response(200, json={"chart": {"result": [], "error": None}})

    with _client(handler) as c:
        hist = yfinance_feed.fetch_history("ZZZ", exchange="NSE", start=date(2025, 1, 1), client=c)
    assert hist.points == []


def test_fetch_history_yahoo_error_raises():
    def handler(_request):
        err = {"code": "Not Found", "description": "No data found, symbol may be delisted"}
        return httpx.Response(200, json={"chart": {"result": None, "error": err}})

    with _client(handler) as c, pytest.raises(PriceFetchError, match="Not Found"):
        yfinance_feed.fetch_history("BADSYM", exchange="NSE", start=date(2025, 1, 1), client=c)


def test_fetch_history_clamps_to_window():
    # Yahoo may return a bar just outside the asked window; we clamp it.
    def handler(_request):
        return httpx.Response(
            200,
            json=_history_chart([(1746498600, 100.0), (1746671400, 200.0)]),  # 05-06, 05-08
        )

    with _client(handler) as c:
        hist = yfinance_feed.fetch_history(
            "X", exchange="NSE", start=date(2025, 5, 7), end=date(2025, 5, 31), client=c
        )
    assert [p.date for p in hist.points] == [date(2025, 5, 8)]


def test_fetch_history_retries_then_succeeds_on_429():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(200, json=_history_chart([(1746498600, 100.0)]))

    with _client(handler) as c:
        hist = yfinance_feed.fetch_history("X", exchange="NSE", start=date(2025, 5, 1), client=c)
    assert calls["n"] == 3
    assert len(hist.points) == 1


def test_fetch_history_429_exhausts_retries_and_raises():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(429, text="Too Many Requests")

    with _client(handler) as c, pytest.raises(PriceFetchError):
        yfinance_feed.fetch_history("X", exchange="NSE", start=date(2025, 5, 1), client=c)
    assert calls["n"] == yfinance_feed._MAX_RETRIES + 1


def test_fetch_quote_retries_on_429():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(200, json=_chart(2850.5))

    with _client(handler) as c:
        quote = yfinance_feed.fetch_quote("RELIANCE", exchange="NSE", client=c)
    assert calls["n"] == 2
    assert quote.price == Decimal("2850.5")


def test_fetch_history_404_does_not_retry():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404, text="not found")

    with _client(handler) as c, pytest.raises(PriceFetchError):
        yfinance_feed.fetch_history("X", exchange="NSE", start=date(2025, 5, 1), client=c)
    assert calls["n"] == 1
