"""mfapi.in NAV feed wrapper. HTTP mocked via ``httpx.MockTransport``."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from folioman_core.price_feeds import mfapi


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    """Stub the retry backoff so tests don't actually sleep."""
    monkeypatch.setattr(mfapi, "_SLEEP", lambda *_a, **_k: None)


def _client(handler) -> httpx.Client:
    """Build an httpx.Client whose transport invokes ``handler`` for every request."""
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=mfapi.BASE_URL)


_DEFAULT_META = {
    "scheme_code": 122639,
    "scheme_name": "Test Equity Fund",
    "isin_growth": "INF000X00001",
}


def _success(data: list[dict], *, meta: dict | None = None) -> dict:
    return {"meta": meta or _DEFAULT_META, "data": data, "status": "SUCCESS"}


def test_fetch_latest_returns_newest_point():
    def handler(_request):
        return httpx.Response(
            200,
            json=_success(
                [
                    {"date": "27-03-2025", "nav": "75.4123"},
                    {"date": "26-03-2025", "nav": "75.0000"},
                ]
            ),
        )

    with _client(handler) as c:
        point = mfapi.fetch_latest_nav("122639", client=c)

    assert point is not None
    assert point.date == date(2025, 3, 27)
    assert point.nav == Decimal("75.4123")


def test_fetch_latest_returns_none_when_no_data():
    def handler(_request):
        return httpx.Response(200, json=_success([]))

    with _client(handler) as c:
        assert mfapi.fetch_latest_nav("122639", client=c) is None


def test_fetch_history_sorts_oldest_first_and_carries_meta():
    def handler(_request):
        return httpx.Response(
            200,
            json=_success(
                [
                    {"date": "27-03-2025", "nav": "75.4123"},
                    {"date": "25-03-2025", "nav": "74.9000"},
                    {"date": "26-03-2025", "nav": "75.0000"},
                ]
            ),
        )

    with _client(handler) as c:
        history = mfapi.fetch_nav_history("122639", client=c)

    assert [p.date for p in history.points] == [
        date(2025, 3, 25),
        date(2025, 3, 26),
        date(2025, 3, 27),
    ]
    assert history.amfi_code == "122639"
    assert history.isin == "INF000X00001"


def test_fetch_history_applies_since_and_until_bounds():
    def handler(_request):
        return httpx.Response(
            200,
            json=_success(
                [
                    {"date": "01-03-2025", "nav": "70"},
                    {"date": "15-03-2025", "nav": "72"},
                    {"date": "30-03-2025", "nav": "75"},
                ]
            ),
        )

    with _client(handler) as c:
        history = mfapi.fetch_nav_history(
            "122639", client=c, since=date(2025, 3, 10), until=date(2025, 3, 20)
        )

    assert [p.date for p in history.points] == [date(2025, 3, 15)]


def test_status_failure_raises_navfetcherror():
    def handler(_request):
        return httpx.Response(200, json={"status": "INVALID_AMFI_CODE", "data": []})

    with _client(handler) as c, pytest.raises(mfapi.NAVFetchError, match="INVALID_AMFI_CODE"):
        mfapi.fetch_latest_nav("9999999", client=c)


def test_http_error_raises_navfetcherror():
    def handler(_request):
        return httpx.Response(500, text="Internal Server Error")

    with _client(handler) as c, pytest.raises(mfapi.NAVFetchError):
        mfapi.fetch_latest_nav("122639", client=c)


def test_invalid_json_raises_navfetcherror():
    def handler(_request):
        return httpx.Response(200, content=b"<html>oops</html>")

    with _client(handler) as c, pytest.raises(mfapi.NAVFetchError):
        mfapi.fetch_latest_nav("122639", client=c)


def test_transient_5xx_is_retried_then_succeeds():
    """A 5xx is transient — retry and recover rather than failing the caller."""
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=_success([{"date": "27-03-2025", "nav": "75.4"}]))

    with _client(handler) as c:
        point = mfapi.fetch_latest_nav("122639", client=c)

    assert point is not None and calls["n"] == 2  # one retry


def test_permanent_404_is_not_retried():
    """A 404 is permanent — fail fast, no retries (don't hammer the feed)."""
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404, text="not found")

    with _client(handler) as c, pytest.raises(mfapi.NAVFetchError):
        mfapi.fetch_latest_nav("122639", client=c)

    assert calls["n"] == 1  # no retry on a permanent error


def test_malformed_rows_are_skipped_not_fatal():
    def handler(_request):
        return httpx.Response(
            200,
            json=_success(
                [
                    {"date": "27-03-2025", "nav": "75.4123"},
                    {"date": "not-a-date", "nav": "70"},
                    {"date": "26-03-2025", "nav": "not_a_decimal"},
                    {"date": None, "nav": "65"},
                    {"date": "25-03-2025", "nav": "74.5"},
                ]
            ),
        )

    with _client(handler) as c:
        history = mfapi.fetch_nav_history("122639", client=c)

    assert [p.date for p in history.points] == [date(2025, 3, 25), date(2025, 3, 27)]


def test_nav_history_helpers():
    """``NAVHistory.latest`` / ``nav_on`` — the consumer-side surface."""
    from folioman_core.models.nav import NAVHistory, NAVPoint

    history = NAVHistory(
        amfi_code="122639",
        points=[
            NAVPoint(date=date(2025, 3, 1), nav=Decimal("70")),
            NAVPoint(date=date(2025, 3, 15), nav=Decimal("72")),
            NAVPoint(date=date(2025, 3, 28), nav=Decimal("75")),
        ],
    )
    assert history.latest().nav == Decimal("75")
    assert history.nav_on(date(2025, 3, 15)) == Decimal("72")
    # Weekend / pre-NAV-publication fallback: use most recent prior NAV.
    assert history.nav_on(date(2025, 3, 20)) == Decimal("72")
    # Before the series — no NAV available.
    assert history.nav_on(date(2025, 2, 28)) is None


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
                json=_success([{"date": "27-03-2025", "nav": "75.4"}]),
                request=httpx.Request("GET", mfapi.BASE_URL + path),
            )

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(mfapi.httpx, "Client", _StubClient)

    point = mfapi.fetch_latest_nav("122639")
    assert point is not None
    assert captured == {
        "base_url": mfapi.BASE_URL,
        "path": "/mf/122639/latest",
        "closed": True,
    }
