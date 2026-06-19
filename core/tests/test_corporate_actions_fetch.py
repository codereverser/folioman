"""NSE/BSE corporate-action fetch — date-ranged, chunked, via shared client."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from folioman_core.corporate_action_subject import CorpActionType, parse_subject
from folioman_core.price_feeds import corporate_actions_fetch
from folioman_core.price_feeds.corporate_actions_fetch import (
    CorporateActionFetchError,
    CorporateActionThrottled,
    _bse_purpose_to_subject,
    _bse_row_to_event,
    _normalize_subject,
    _nse_row_to_event,
    _pace,
    _parse_bse_date,
    _parse_nse_date,
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


def test_fetch_uses_a_single_window_and_dedupes():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "from_date" in request.url.params:
            calls.append((request.url.params["from_date"], request.url.params["to_date"]))
        return httpx.Response(200, json=_HDFCBANK_ROWS)

    client = _wrap(handler)
    events = fetch_nse_corporate_actions(
        "HDFCBANK", start=date(2016, 1, 1), end=date(2026, 6, 1), client=client
    )
    # A decade of history is one request now, not ~10 one-year chunks.
    assert len(calls) == 1
    assert len(events) == 1


def test_windows_still_chunks_beyond_the_cap():
    from folioman_core.price_feeds.corporate_actions_fetch import _windows

    # Within the cap → one window; far beyond it → the safety-net chunker splits.
    assert len(list(_windows(date(2016, 1, 1), date(2026, 6, 1)))) == 1
    assert len(list(_windows(date(1990, 1, 1), date(2026, 6, 1)))) > 1


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


# --- date / row parsing edge cases ------------------------------------------


@pytest.mark.parametrize("text", ["", "-", "not-a-date", "31-Foo-2020"])
def test_parse_nse_date_returns_none_on_unparseable(text):
    assert _parse_nse_date(text) is None


def test_parse_nse_date_parses_standard_form():
    assert _parse_nse_date("26-Aug-2025") == date(2025, 8, 26)


@pytest.mark.parametrize("text", ["", "20259999", "garbage"])
def test_parse_bse_date_returns_none_on_unparseable(text):
    assert _parse_bse_date(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("20160629", date(2016, 6, 29)), ("29 Jun 2016", date(2016, 6, 29))],
)
def test_parse_bse_date_accepts_both_forms(text, expected):
    assert _parse_bse_date(text) == expected


def test_nse_row_without_date_or_subject_is_dropped():
    assert _nse_row_to_event({"subject": "Bonus 1:1"}) is None  # no exDate
    assert _nse_row_to_event({"exDate": "26-Aug-2025"}) is None  # no subject


def test_bse_row_without_date_or_purpose_is_dropped():
    assert _bse_row_to_event({"Purpose": "Bonus 1:1"}) is None  # no ex date
    assert _bse_row_to_event({"exdate": "20250826"}) is None  # no purpose


def test_bse_purpose_passthrough_for_blank_and_non_dividend():
    assert _bse_purpose_to_subject("") == ""
    assert _bse_purpose_to_subject("Bonus 1:1") == "Bonus 1:1"


def test_pace_sleeps(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(corporate_actions_fetch, "_SLEEP", slept.append)
    _pace()
    assert len(slept) == 1 and slept[0] >= corporate_actions_fetch._BASE_SPACING


# --- empty inputs -----------------------------------------------------------


def test_fetch_nse_empty_symbol_returns_empty():
    assert fetch_nse_corporate_actions("") == []


def test_fetch_bse_no_symbol_or_scripcode_returns_empty():
    assert fetch_bse_corporate_actions("") == []


def test_lookup_empty_symbol_returns_none():
    client = _wrap_bse(lambda _r: httpx.Response(200, text=""))
    assert lookup_bse_scripcode("", client=client) is None


# --- throttle (401/403/429): stop, don't retry ------------------------------


@pytest.mark.parametrize("status", [401, 403, 429])
def test_fetch_nse_throttle_raises(status):
    client = _wrap(lambda _r: httpx.Response(status, json=[]))
    with pytest.raises(CorporateActionThrottled):
        fetch_nse_corporate_actions("RELIANCE", client=client)


def test_fetch_bse_throttle_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if "PeerSmartSearch" in str(request.url):
            return httpx.Response(200, text="<li ng-click=\"liclick('500180','X')\">")
        return httpx.Response(429, json=[])

    with pytest.raises(CorporateActionThrottled):
        fetch_bse_corporate_actions("HDFCBANK", client=_wrap_bse(handler))


def test_lookup_throttle_raises():
    with pytest.raises(CorporateActionThrottled):
        lookup_bse_scripcode("TCS", client=_wrap_bse(lambda _r: httpx.Response(403, text="")))


# --- transient HTTP error / bad payloads ------------------------------------


def test_fetch_nse_http_error_raises_when_no_data():
    client = _wrap(lambda _r: httpx.Response(500, json=[]))
    with pytest.raises(CorporateActionFetchError):
        fetch_nse_corporate_actions("RELIANCE", client=client)


def test_fetch_nse_unexpected_shape_raises():
    client = _wrap(lambda _r: httpx.Response(200, json={"not": "a list"}))
    with pytest.raises(CorporateActionFetchError, match="unexpected shape"):
        fetch_nse_corporate_actions("RELIANCE", client=client)


def test_fetch_nse_skips_non_dict_rows():
    client = _wrap(lambda _r: httpx.Response(200, json=["junk", _HDFCBANK_ROWS[0]]))
    events = fetch_nse_corporate_actions("HDFCBANK", client=client)
    assert len(events) == 1


def test_fetch_nse_dedupes_repeated_rows():
    client = _wrap(lambda _r: httpx.Response(200, json=_HDFCBANK_ROWS + _HDFCBANK_ROWS))
    events = fetch_nse_corporate_actions("HDFCBANK", client=client)
    assert len(events) == 1


def test_fetch_bse_unexpected_shape_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if "PeerSmartSearch" in str(request.url):
            return httpx.Response(200, text="<li ng-click=\"liclick('500180','X')\">")
        return httpx.Response(200, json={"not": "a list"})

    with pytest.raises(CorporateActionFetchError, match="unexpected shape"):
        fetch_bse_corporate_actions("HDFCBANK", client=_wrap_bse(handler))


# --- owned-client paths (no client passed) ----------------------------------


def test_fetch_nse_warms_and_closes_its_own_client(monkeypatch):
    closed = {"n": 0}
    inner = _wrap(_nse_handler({"HDFCBANK": _HDFCBANK_ROWS}))
    monkeypatch.setattr(inner, "close", lambda: closed.__setitem__("n", closed["n"] + 1))
    monkeypatch.setattr(corporate_actions_fetch, "warmed_nse_client", lambda: inner)
    events = fetch_nse_corporate_actions("HDFCBANK")
    assert len(events) == 1 and closed["n"] == 1


def test_fetch_bse_no_scrip_returns_empty_and_closes(monkeypatch):
    closed = {"n": 0}
    inner = _wrap_bse(lambda _r: httpx.Response(200, text="<html>nothing</html>"))
    monkeypatch.setattr(inner, "close", lambda: closed.__setitem__("n", closed["n"] + 1))
    monkeypatch.setattr(corporate_actions_fetch, "warmed_bse_client", lambda: inner)
    assert fetch_bse_corporate_actions("NOPE") == []
    assert closed["n"] == 1


def test_fetch_bse_lookup_outage_closes_and_raises(monkeypatch):
    closed = {"n": 0}
    inner = _wrap_bse(lambda _r: httpx.Response(404, text="down"))
    monkeypatch.setattr(inner, "close", lambda: closed.__setitem__("n", closed["n"] + 1))
    monkeypatch.setattr(corporate_actions_fetch, "warmed_bse_client", lambda: inner)
    with pytest.raises(CorporateActionFetchError):
        fetch_bse_corporate_actions("TCS")
    assert closed["n"] == 1


# --- routing ----------------------------------------------------------------


def test_fetch_corporate_actions_routes_bse_only():
    def handler(request: httpx.Request) -> httpx.Response:
        if "PeerSmartSearch" in str(request.url):
            return httpx.Response(200, text="<li ng-click=\"liclick('500180','HDFC BANK')\">")
        return httpx.Response(200, json=[_BSE_DIVIDEND_ROW])

    events = fetch_corporate_actions(
        "HDFCBANK",
        exchange="BSE",
        start=date(2016, 1, 1),
        end=date(2016, 12, 31),
        bse=_wrap_bse(handler),
    )
    assert events and all(e.exchange == "BSE" for e in events)


def test_fetch_corporate_actions_merges_bse_only_rows():
    bse_row = {
        "short_name": "HDFCBANK",
        "exdate": "20240101",
        "Purpose": "Dividend - Rs 5 Per Share",
        "isin": "INE040A01018",
    }

    def bse_handler(request: httpx.Request) -> httpx.Response:
        if "PeerSmartSearch" in str(request.url):
            return httpx.Response(200, text="<li ng-click=\"liclick('500180','HDFC BANK')\">")
        return httpx.Response(200, json=[bse_row])

    events = fetch_corporate_actions(
        "HDFCBANK",
        exchange="",
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        nse=_wrap(_nse_handler({"HDFCBANK": _HDFCBANK_ROWS})),
        bse=_wrap_bse(bse_handler),
    )
    # NSE bonus + the BSE-only dividend both survive the merge.
    assert {e.exchange for e in events} == {"NSE", "BSE"}


def test_fetch_corporate_actions_tolerates_bse_failure_when_unrouted():
    def bse_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json=[])

    events = fetch_corporate_actions(
        "HDFCBANK",
        exchange="",
        nse=_wrap(_nse_handler({"HDFCBANK": _HDFCBANK_ROWS})),
        bse=_wrap_bse(bse_handler),
    )
    # BSE failing on a blank-exchange fetch must not sink the NSE result.
    assert len(events) == 1 and events[0].exchange == "NSE"
