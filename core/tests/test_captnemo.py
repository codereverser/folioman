"""captnemo (mf.captnemo.in) NAV feed wrapper. HTTP mocked via ``httpx.MockTransport``."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from folioman_core.price_feeds import captnemo


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    """Stub the retry backoff so tests don't actually sleep."""
    monkeypatch.setattr(captnemo, "_SLEEP", lambda *_a, **_k: None)


def _client(handler) -> httpx.Client:
    """Build an httpx.Client whose transport invokes ``handler`` for every request."""
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=captnemo.BASE_URL)


def _payload(historical, *, isin="INF179K01608", latest_date=None, latest_nav=None) -> dict:
    newest = historical[-1] if historical else [None, None]
    return {
        "ISIN": isin,
        "name": "Test Fund",
        "date": latest_date if latest_date is not None else newest[0],
        "nav": latest_nav if latest_nav is not None else newest[1],
        "historical_nav": historical,
    }


def test_fetch_latest_uses_top_level_point():
    def handler(_request):
        return httpx.Response(
            200,
            json=_payload(
                [["2025-03-26", 75.0], ["2025-03-27", 75.4123]],
            ),
        )

    with _client(handler) as c:
        point = captnemo.fetch_latest_nav("INF179K01608", client=c)

    assert point is not None
    assert point.date == date(2025, 3, 27)
    assert point.nav == Decimal("75.4123")


def test_float_nav_parses_without_binary_noise():
    """JSON floats route through ``Decimal(str(...))`` — no 1924.0809999... drift."""

    def handler(_request):
        return httpx.Response(200, json=_payload([["2026-06-11", 1924.081]]))

    with _client(handler) as c:
        point = captnemo.fetch_latest_nav("INF179K01608", client=c)

    assert point.nav == Decimal("1924.081")


def test_fetch_history_sorts_oldest_first_and_carries_isin():
    def handler(_request):
        return httpx.Response(
            200,
            json=_payload(
                [["2025-03-27", 75.4123], ["2025-03-25", 74.9], ["2025-03-26", 75.0]],
            ),
        )

    with _client(handler) as c:
        history = captnemo.fetch_nav_history("INF179K01608", client=c)

    assert [p.date for p in history.points] == [
        date(2025, 3, 25),
        date(2025, 3, 26),
        date(2025, 3, 27),
    ]
    assert history.isin == "INF179K01608"


def test_fetch_history_applies_since_and_until_bounds():
    def handler(_request):
        return httpx.Response(
            200,
            json=_payload(
                [["2025-03-01", 70.0], ["2025-03-15", 72.0], ["2025-03-30", 75.0]],
            ),
        )

    with _client(handler) as c:
        history = captnemo.fetch_nav_history(
            "INF179K01608", client=c, since=date(2025, 3, 10), until=date(2025, 3, 20)
        )

    assert [p.date for p in history.points] == [date(2025, 3, 15)]


def test_fetch_latest_returns_none_when_no_data():
    def handler(_request):
        return httpx.Response(200, json=_payload([]))

    with _client(handler) as c:
        assert captnemo.fetch_latest_nav("INF179K01608", client=c) is None


def test_unexpected_shape_raises_navfetcherror():
    """An error body with no ``historical_nav`` is unusable — fail, don't return empty."""

    def handler(_request):
        return httpx.Response(200, json={"error": "Invalid ISIN"})

    with _client(handler) as c, pytest.raises(captnemo.NAVFetchError):
        captnemo.fetch_latest_nav("INF000000000", client=c)


def test_http_error_raises_navfetcherror():
    def handler(_request):
        return httpx.Response(500, text="Internal Server Error")

    with _client(handler) as c, pytest.raises(captnemo.NAVFetchError):
        captnemo.fetch_latest_nav("INF179K01608", client=c)


def test_invalid_json_raises_navfetcherror():
    def handler(_request):
        return httpx.Response(200, content=b"<html>oops</html>")

    with _client(handler) as c, pytest.raises(captnemo.NAVFetchError):
        captnemo.fetch_latest_nav("INF179K01608", client=c)


def test_transient_5xx_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=_payload([["2025-03-27", 75.4]]))

    with _client(handler) as c:
        point = captnemo.fetch_latest_nav("INF179K01608", client=c)

    assert point is not None and calls["n"] == 2  # one retry


def test_permanent_404_is_not_retried():
    """A 404 (unknown ISIN) is permanent — fail fast, no retries."""
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404, json={"error": "Invalid ISIN"})

    with _client(handler) as c, pytest.raises(captnemo.NAVFetchError):
        captnemo.fetch_latest_nav("INF000000000", client=c)

    assert calls["n"] == 1  # no retry on a permanent error


def test_malformed_rows_are_skipped_not_fatal():
    def handler(_request):
        return httpx.Response(
            200,
            json=_payload(
                [
                    ["2025-03-25", 74.5],
                    ["not-a-date", 70.0],
                    ["2025-03-26", None],
                    ["2025-03-27", 75.4123],
                ],
                latest_date="2025-03-27",
                latest_nav=75.4123,
            ),
        )

    with _client(handler) as c:
        history = captnemo.fetch_nav_history("INF179K01608", client=c)

    assert [p.date for p in history.points] == [date(2025, 3, 25), date(2025, 3, 27)]


def test_fetch_works_without_explicit_client(monkeypatch):
    """When no client is passed, the helper builds and disposes one internally."""
    captured: dict = {}

    class _StubClient:
        def __init__(self, base_url: str, timeout: float):
            captured["base_url"] = base_url

        def __enter__(self):
            return self

        def get(self, path):
            captured["path"] = path
            return httpx.Response(
                200,
                json=_payload([["2025-03-27", 75.4]]),
                request=httpx.Request("GET", captnemo.BASE_URL + path),
            )

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(captnemo.httpx, "Client", _StubClient)

    point = captnemo.fetch_latest_nav("INF179K01608")
    assert point is not None
    assert captured == {
        "base_url": captnemo.BASE_URL,
        "path": "/nav/INF179K01608",
        "closed": True,
    }
