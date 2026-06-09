"""NSE direct quote feed (the yfinance fallback). HTTP mocked."""

from datetime import date
from decimal import Decimal

import httpx
from folioman_core.price_feeds import nse_bse


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=nse_bse._NSE_BASE)


def test_fetch_quote_success_returns_today_inr():
    def handler(_request):
        return httpx.Response(200, json={"priceInfo": {"lastPrice": 2850.0}})

    with _client(handler) as c:
        quote = nse_bse.fetch_quote("RELIANCE", client=c)

    assert quote is not None
    assert quote.price == Decimal("2850.0")
    assert quote.currency == "INR"
    assert quote.source == "nse"
    assert quote.as_of == date.today()


def test_fetch_quote_cookie_wall_401_returns_none():
    """NSE's cookie wall typically responds with 401/403 to bare requests."""

    def handler(_request):
        return httpx.Response(401, text="Unauthorized")

    with _client(handler) as c:
        assert nse_bse.fetch_quote("RELIANCE", client=c) is None


def test_fetch_quote_network_error_returns_none():
    def handler(_request):
        raise httpx.ConnectError("simulated network failure")

    with _client(handler) as c:
        assert nse_bse.fetch_quote("RELIANCE", client=c) is None


def test_fetch_quote_malformed_json_returns_none():
    def handler(_request):
        return httpx.Response(200, content=b"<html>not json</html>")

    with _client(handler) as c:
        assert nse_bse.fetch_quote("RELIANCE", client=c) is None


def test_fetch_quote_missing_price_field_returns_none():
    def handler(_request):
        return httpx.Response(200, json={"priceInfo": {}})

    with _client(handler) as c:
        assert nse_bse.fetch_quote("RELIANCE", client=c) is None


def test_cold_fetch_quote_warms_cookies_before_the_api_call(monkeypatch):
    # A lone (clientless) call must prime the session via the get-quotes page
    # before hitting /api/quote-equity — without it NSE answers 403 and every
    # quote falls through to the rate-limited Yahoo fallback.
    paths: list[str] = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == nse_bse._WARMUP_PATH:
            return httpx.Response(200, text="ok")  # cookie-priming page
        return httpx.Response(200, json={"priceInfo": {"lastPrice": 2850.0}})

    real_client = httpx.Client

    def fake_client(**kwargs):
        return real_client(
            transport=httpx.MockTransport(handler),
            base_url=nse_bse._NSE_BASE,
            follow_redirects=True,
        )

    monkeypatch.setattr(httpx, "Client", fake_client)
    quote = nse_bse.fetch_quote("RELIANCE")  # no client → warms its own

    assert quote is not None and quote.price == Decimal("2850.0")
    assert paths[0] == nse_bse._WARMUP_PATH  # warmup happened first
    assert paths[1].endswith("/api/quote-equity")
