"""NSE/BSE corporate-action fetch — date-ranged, chunked, via shared client."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from folioman_core.corporate_action_subject import CorpActionType, parse_subject
from folioman_core.price_feeds import corporate_actions_fetch
from folioman_core.price_feeds.corporate_actions_fetch import (
    CorporateActionFetchError,
    _bse_purpose_to_subject,
    _normalize_subject,
    fetch_bse_corporate_actions,
    fetch_corporate_actions,
    fetch_nse_corporate_actions,
    lookup_bse_scripcode,
)
from folioman_core.price_feeds.nse_bse_client import BSE_BASE_URL, NSE_BASE_URL, ExchangeClient


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(corporate_actions_fetch, "_SLEEP", lambda _s: None)


def _nse_handler(payload_by_symbol: dict[str, list[dict]]):
    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.params.get("symbol", "")
        rows = payload_by_symbol.get(symbol, [])
        return httpx.Response(200, json=rows)

    return handler


def _wrap(handler) -> ExchangeClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url=NSE_BASE_URL)
    return ExchangeClient(base_url=NSE_BASE_URL, warmup_path="/warm", client=http).warm()


def _wrap_bse(handler) -> ExchangeClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url=BSE_BASE_URL)
    return ExchangeClient(base_url=BSE_BASE_URL, warmup_path="/", client=http).warm()


_ALLCARGO_ROWS = [
    {
        "symbol": "ALLCARGO",
        "series": "EQ",
        "subject": "Demerger",
        "exDate": "18-Apr-2023",
        "recDate": "18-Apr-2023",
        "isin": "INE418H01026",
        "comp": "Allcargo Logistics Ltd",
        "faceVal": "2",
    },
    {
        "symbol": "ALLCARGO",
        "series": "EQ",
        "subject": "Bonus 3:1",
        "exDate": "02-Jan-2024",
        "recDate": "02-Jan-2024",
        "isin": "INE418H01026",
        "comp": "Allcargo Logistics Ltd",
        "faceVal": "2",
    },
    {
        "symbol": "ALLCARGO",
        "series": "EQ",
        "subject": "Demerger",
        "exDate": "12-Nov-2025",
        "recDate": "12-Nov-2025",
        "isin": "INE418H01026",
        "comp": "Allcargo Logistics Ltd",
        "faceVal": "2",
    },
]

_HDFCBANK_ROWS = [
    {
        "symbol": "HDFCBANK",
        "series": "EQ",
        "subject": "Bonus 1:1",
        "exDate": "26-Aug-2025",
        "recDate": "26-Aug-2025",
        "isin": "INE040A01018",
        "comp": "HDFC Bank Ltd",
        "faceVal": "1",
    },
]


def test_fetch_nse_returns_bonus_and_demergers_for_allcargo():
    client = _wrap(_nse_handler({"ALLCARGO": _ALLCARGO_ROWS}))
    events = fetch_nse_corporate_actions(
        "ALLCARGO", start=date(2016, 1, 1), end=date(2026, 6, 15), client=client
    )
    subjects = [e.subject for e in events]
    assert "Bonus 3:1" in subjects
    assert sum(1 for s in subjects if s == "Demerger") == 2
    bonus = next(e for e in events if e.subject == "Bonus 3:1")
    parsed = parse_subject(bonus.subject)
    assert parsed.type is CorpActionType.BONUS
    assert parsed.unit_multiplier is not None
    assert parsed.unit_multiplier == pytest.approx(4)


def test_fetch_nse_returns_hdfcbank_bonus():
    client = _wrap(_nse_handler({"HDFCBANK": _HDFCBANK_ROWS}))
    events = fetch_nse_corporate_actions(
        "HDFCBANK", start=date(2016, 1, 1), end=date(2026, 6, 15), client=client
    )
    assert len(events) == 1
    assert events[0].subject == "Bonus 1:1"
    assert parse_subject(events[0].subject).unit_multiplier == pytest.approx(2)


def test_fetch_chunks_merge_without_dupes():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "from_date" in request.url.params:
            calls.append((request.url.params["from_date"], request.url.params["to_date"]))
        return httpx.Response(200, json=_HDFCBANK_ROWS)

    client = _wrap(handler)
    fetch_nse_corporate_actions(
        "HDFCBANK", start=date(2016, 1, 1), end=date(2017, 6, 1), client=client
    )
    assert len(calls) == 2  # 2016 + 2017 partial
    events = fetch_nse_corporate_actions(
        "HDFCBANK", start=date(2016, 1, 1), end=date(2017, 6, 1), client=client
    )
    assert len(events) == 1


def test_non_json_raises_when_no_data():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>blocked</html>")

    client = _wrap(handler)
    with pytest.raises(CorporateActionFetchError):
        fetch_nse_corporate_actions("RELIANCE", client=client)


def test_fetch_corporate_actions_routes_nse():
    client = _wrap(_nse_handler({"INFY": []}))
    assert fetch_corporate_actions("INFY", exchange="NSE", nse=client) == []


def test_normalize_subject_collapses_whitespace():
    assert _normalize_subject("  Bonus   3 : 1  ") == "Bonus 3 : 1"


def test_bse_purpose_normalises_dividend_with_decimal():
    subject = _bse_purpose_to_subject("Dividend - Rs. - 9.5000")
    assert subject == "Dividend - Rs 9.5000 Per Share"
    parsed = parse_subject(subject)
    assert parsed.type is CorpActionType.DIVIDEND
    assert parsed.amount is not None


_BSE_DIVIDEND_ROW = {
    "scrip_code": 500180,
    "short_name": "HDFCBANK",
    "exdate": "20160629",
    "Ex_date": "29 Jun 2016",
    "Purpose": "Dividend - Rs. - 9.5000",
    "RD_Date": "30 Jun 2016",
    "long_name": "HDFC Bank Ltd",
}


def test_fetch_bse_corporate_actions_parses_row():
    lookup_html = "<li ng-click=\"liclick('500180','HDFC BANK LTD')\"><a>HDFC BANK LTD</a></li>"

    def handler(request: httpx.Request) -> httpx.Response:
        if "PeerSmartSearch" in str(request.url):
            return httpx.Response(200, text=lookup_html)
        return httpx.Response(200, json=[_BSE_DIVIDEND_ROW])

    client = _wrap_bse(handler)
    events = fetch_bse_corporate_actions(
        "HDFCBANK", start=date(2016, 1, 1), end=date(2016, 12, 31), client=client
    )
    assert len(events) == 1
    assert events[0].exchange == "BSE"
    assert events[0].subject == "Dividend - Rs 9.5000 Per Share"


def test_lookup_bse_scripcode():
    html = "<li ng-click=\"liclick('532540','TCS')\">"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    client = _wrap_bse(handler)
    assert lookup_bse_scripcode("TCS", client=client) == "532540"


# PeerSmartSearch is a fuzzy prefix match: "HDFC" returns HDFC BANK first, then
# HDFC LIFE, HDFC AMC — each with its own ISIN and scrip code.
_HDFC_SEARCH_HTML = (
    "<li ng-click=\"liclick('500180','HDFC BANK LTD')\"><a>HDFC BANK LTD<br />"
    "<span>HDFCBANK&nbsp;&nbsp;&nbsp;INE040A01034&nbsp;&nbsp;&nbsp;500180</span></a></li>"
    "<li ng-click=\"liclick('540777','HDFC LIFE')\"><a>HDFC LIFE<br />"
    "<span>HDFCLIFE&nbsp;&nbsp;&nbsp;INE795G01014&nbsp;&nbsp;&nbsp;540777</span></a></li>"
)


def test_lookup_bse_scripcode_matches_expected_isin():
    """The fuzzy first hit (HDFC BANK) is rejected; the scrip whose ISIN matches wins."""
    client = _wrap_bse(lambda _r: httpx.Response(200, text=_HDFC_SEARCH_HTML))
    assert lookup_bse_scripcode("HDFC", client=client, expected_isin="INE795G01014") == "540777"


def test_lookup_bse_scripcode_no_isin_match_returns_none():
    """A delisted security whose ISIN isn't in the results gets no scrip — never a
    same-prefix different company (the HDFC -> HDFC BANK mis-filing)."""
    client = _wrap_bse(lambda _r: httpx.Response(200, text=_HDFC_SEARCH_HTML))
    assert lookup_bse_scripcode("HDFC", client=client, expected_isin="INE001A01036") is None


def test_lookup_bse_scripcode_no_match_returns_none():
    # A 200 with no scrip in the body is a genuine miss → None.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>nothing here</html>")

    assert lookup_bse_scripcode("NOPE", client=_wrap_bse(handler)) is None


def test_lookup_bse_scripcode_outage_raises():
    # A failed request (non-200) is an outage, not "no such scrip" — raise so the
    # caller records it instead of reporting BSE as having no corporate actions.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")  # non-retryable → returned at once

    with pytest.raises(CorporateActionFetchError):
        lookup_bse_scripcode("TCS", client=_wrap_bse(handler))


def test_cross_feed_dedupes_same_action():
    nse_row = {
        "symbol": "HDFCBANK",
        "subject": "Bonus 1:1",
        "exDate": "26-Aug-2025",
        "recDate": "26-Aug-2025",
        "isin": "INE040A01018",
    }
    bse_row = {
        "short_name": "HDFCBANK",
        "exdate": "20250826",
        "Purpose": "Bonus 1:1",
        "isin": "INE040A01018",
    }
    lookup_html = "<li ng-click=\"liclick('500180','HDFC BANK LTD')\">"

    def nse_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[nse_row])

    def bse_handler(request: httpx.Request) -> httpx.Response:
        if "PeerSmartSearch" in str(request.url):
            return httpx.Response(200, text=lookup_html)
        return httpx.Response(200, json=[bse_row])

    events = fetch_corporate_actions(
        "HDFCBANK",
        exchange="",
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        nse=_wrap(nse_handler),
        bse=_wrap_bse(bse_handler),
    )
    assert len(events) == 1
    assert events[0].exchange == "NSE"


def test_bounded_json_rejects_oversized_response():
    from folioman_core.price_feeds.corporate_actions_fetch import (
        _MAX_RESPONSE_BYTES,
        _bounded_json,
    )

    body = b"x" * (_MAX_RESPONSE_BYTES + 1)
    response = httpx.Response(200, content=body)
    with pytest.raises(CorporateActionFetchError, match="too large"):
        _bounded_json(response)
