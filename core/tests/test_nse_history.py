"""NSE security-wise historical feed (CSV export). HTTP mocked — no network, no
cookie warmup (passing a client skips the warmup path)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from folioman_core.price_feeds import nse_history
from folioman_core.price_feeds.nse_bse_client import NSE_BASE_URL
from folioman_core.price_feeds.yfinance_feed import PriceFetchError

# Header mirrors NSE's real CSV: BOM on the first cell, trailing spaces on names.
_HEADER = '﻿"Symbol  ","Series  ","Date  ","Prev Close  ","Close Price  "'


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(nse_history, "_SLEEP", lambda _s: None)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=NSE_BASE_URL)


def _csv(*rows: tuple[str, str]) -> str:
    """Build a security-wise CSV body from (date, close) rows."""
    lines = [_HEADER]
    for ts, close in rows:
        lines.append(f'"RELIANCE","EQ","{ts}","0.00","{close}"')
    return "\n".join(lines)


def _csv_response(*rows: tuple[str, str]) -> httpx.Response:
    return httpx.Response(200, text=_csv(*rows), headers={"Content-Type": "text/csv"})


def test_fetch_history_parses_close_oldest_first_with_commas():
    def handler(request):
        assert request.url.path == nse_history._HISTORY_PATH
        assert request.url.params["csv"] == "true"  # CSV export, not the capped JSON
        # NSE returns most-recent-first; comma-grouped, quoted values.
        return _csv_response(("03-Jan-2024", "1,410.50"), ("01-Jan-2024", "1,400.00"))

    with _client(handler) as c:
        hist = nse_history.fetch_history(
            "RELIANCE", start=date(2024, 1, 1), end=date(2024, 1, 31), client=c
        )
    assert [(p.date, p.nav) for p in hist.points] == [
        (date(2024, 1, 1), Decimal("1400.00")),
        (date(2024, 1, 3), Decimal("1410.50")),
    ]


def test_fetch_history_skips_rows_missing_close_or_date():
    def handler(_request):
        return _csv_response(("01-Jan-2024", "100.00"), ("", "5.00"), ("02-Jan-2024", ""))

    with _client(handler) as c:
        hist = nse_history.fetch_history(
            "X", start=date(2024, 1, 1), end=date(2024, 1, 31), client=c
        )
    assert [(p.date, p.nav) for p in hist.points] == [(date(2024, 1, 1), Decimal("100.00"))]


def test_fetch_history_chunks_wide_range_into_sub_requests():
    windows: list[tuple[str, str]] = []

    def handler(request):
        q = dict(request.url.params)
        windows.append((q["from"], q["to"]))
        return _csv_response()

    with _client(handler) as c:
        # 2 years with ~1-year windows → at least 2 chunked requests.
        nse_history.fetch_history("X", start=date(2023, 1, 1), end=date(2024, 12, 31), client=c)
    assert len(windows) >= 2
    assert windows[0][0] == "01-01-2023"  # contiguous, covering the whole range
    assert windows[-1][1] == "31-12-2024"


def test_fetch_history_clamps_rows_outside_window():
    def handler(_request):
        return _csv_response(("01-Jan-2024", "100.00"), ("15-Feb-2024", "200.00"))

    with _client(handler) as c:
        hist = nse_history.fetch_history(
            "X", start=date(2024, 2, 1), end=date(2024, 2, 28), client=c
        )
    assert [p.date for p in hist.points] == [date(2024, 2, 15)]


def test_fetch_history_non_csv_response_raises_when_no_data():
    # NSE serves an HTML error/WAF page when overloaded — not CSV.
    def handler(_request):
        return httpx.Response(
            200, text="<!DOCTYPE html><html>blocked</html>", headers={"Content-Type": "text/html"}
        )

    with _client(handler) as c, pytest.raises(PriceFetchError):
        nse_history.fetch_history("X", start=date(2024, 1, 1), end=date(2024, 1, 31), client=c)


def test_fetch_history_http_error_raises_when_no_data():
    def handler(_request):
        return httpx.Response(429, text="too many requests")

    with _client(handler) as c, pytest.raises(PriceFetchError):
        nse_history.fetch_history("X", start=date(2024, 1, 1), end=date(2024, 1, 31), client=c)


def test_fetch_history_returns_partial_when_some_chunks_fail():
    # Two ~1-year chunks: the first (older) errors, the second returns data.
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                200, text="<html>blocked</html>", headers={"Content-Type": "text/html"}
            )
        return _csv_response(("02-Jan-2024", "111.00"))

    with _client(handler) as c:
        hist = nse_history.fetch_history(
            "X", start=date(2023, 1, 1), end=date(2024, 12, 31), client=c
        )
    # Partial result kept — the good chunk's point survives, no raise.
    assert [p.date for p in hist.points] == [date(2024, 1, 2)]


def test_fetch_history_empty_symbol_returns_empty():
    assert nse_history.fetch_history("", start=date(2024, 1, 1)).points == []
