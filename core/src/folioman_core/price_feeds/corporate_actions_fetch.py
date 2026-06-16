"""NSE/BSE corporate-action feeds — date-ranged fetch via the shared exchange client.

NSE exposes ``/api/corporates-corporateActions`` with optional ``symbol`` and
``from_date`` / ``to_date`` (``DD-MM-YYYY``). Without a date range the feed
defaults to a recent-only window, so historical bonuses (e.g. INFY 2018,
POWERGRID 2021) are missed unless the caller passes an explicit range.

BSE serves the same data from ``api.bseindia.com`` (``/DefaultData/w``) keyed
by BSE scrip code; a symbol lookup hits ``/PeerSmartSearch/w`` when no code is
known. Both routes reuse :func:`~folioman_core.price_feeds.nse_bse_client.nse_client`
/ :func:`~folioman_core.price_feeds.nse_bse_client.bse_client` for cookie
warm-up and transient retry.

Wide ranges are fetched in ~1-year chunks so a single symbol cannot trip an
undocumented per-response cap.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from folioman_core.models.corporate_action import CorporateActionEvent
from folioman_core.price_feeds.nse_bse_client import (
    BROWSER_HEADERS,
    ExchangeClient,
    bse_client,
    nse_client,
)

_NSE_PATH = "/api/corporates-corporateActions"
_NSE_REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-actions"
_BSE_API_BASE = "https://api.bseindia.com/BseIndiaAPI/api"
_BSE_ACTIONS_PATH = f"{_BSE_API_BASE}/DefaultData/w"
_BSE_LOOKUP_PATH = f"{_BSE_API_BASE}/PeerSmartSearch/w"
_BSE_REFERER = "https://www.bseindia.com/corporates/corporate_act.aspx"

DEFAULT_EARLIEST = date(2016, 1, 1)
_MAX_WINDOW_DAYS = 365
_CHUNK_SPACING = 0.2
_SLEEP = time.sleep

_BSE_SCRIP = re.compile(r"liclick\('(\d+)'", re.IGNORECASE)
_BSE_RS_AMOUNT = re.compile(r"(?:rs\.?|inr|₹)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


class CorporateActionFetchError(Exception):
    """Raised when no chunk yielded parseable corporate-action data."""


def warmed_nse_client() -> ExchangeClient:
    """Return an NSE client with session cookies primed."""
    return nse_client()


def warmed_bse_client() -> ExchangeClient:
    """Return a BSE client with session cookies primed."""
    return bse_client()


def _normalize_subject(subject: str) -> str:
    """Collapse whitespace so feed/API drift does not split cache rows."""
    return " ".join((subject or "").split())


def _windows(start: date, end: date):
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=_MAX_WINDOW_DAYS - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def _parse_nse_date(text: str) -> date | None:
    text = (text or "").strip()
    if not text or text == "-":
        return None
    for fmt in ("%d-%b-%Y",):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_bse_date(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit() and len(text) == 8:
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    for fmt in ("%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _nse_row_to_event(row: dict) -> CorporateActionEvent | None:
    ex_date = _parse_nse_date(str(row.get("exDate", "")))
    subject = _normalize_subject(str(row.get("subject", "")))
    if ex_date is None or not subject:
        return None
    rec = _parse_nse_date(str(row.get("recDate", "")))
    return CorporateActionEvent(
        symbol=str(row.get("symbol", "")).strip().upper(),
        subject=subject,
        ex_date=ex_date,
        isin=str(row.get("isin", "")).strip().upper(),
        series=str(row.get("series", "")).strip(),
        record_date=rec,
        exchange="NSE",
        company_name=str(row.get("comp", "")).strip(),
        face_value=str(row.get("faceVal", "")).strip(),
    )


def _bse_purpose_to_subject(purpose: str) -> str:
    """Normalise BSE ``Purpose`` into an NSE-style subject where possible."""
    raw = (purpose or "").strip()
    if not raw:
        return raw
    # BSE often emits "Dividend - Rs. - 9.5000" (amount after a bare Rs. token).
    if "dividend" in raw.lower() and "per share" not in raw.lower():
        amount_match = _BSE_RS_AMOUNT.search(raw)
        if amount_match is None:
            trailing = re.search(r"(\d+(?:\.\d+)?)\s*$", raw)
            if trailing:
                amount_match = trailing
        if amount_match:
            try:
                amount = Decimal(amount_match.group(1))
            except InvalidOperation:
                return raw
            label = raw.split("-", 1)[0].strip() or "Dividend"
            return f"{label} - Rs {amount} Per Share"
    return raw


def _bse_row_to_event(row: dict) -> CorporateActionEvent | None:
    ex_date = _parse_bse_date(str(row.get("exdate", ""))) or _parse_bse_date(
        str(row.get("Ex_date", ""))
    )
    purpose = str(row.get("Purpose", "")).strip()
    if ex_date is None or not purpose:
        return None
    rec = _parse_bse_date(str(row.get("RD_Date", "")))
    isin = ""
    for key in ("isin", "ISIN", "scrip_isin"):
        if row.get(key):
            isin = str(row[key]).strip().upper()
            break
    return CorporateActionEvent(
        symbol=str(row.get("short_name", "")).strip().upper(),
        subject=_normalize_subject(_bse_purpose_to_subject(purpose)),
        ex_date=ex_date,
        isin=isin,
        record_date=rec,
        exchange="BSE",
        company_name=str(row.get("long_name", "")).strip(),
    )


def _within_feed_key(event: CorporateActionEvent) -> tuple:
    """Dedupe key inside one exchange feed (chunk seams)."""
    return (event.isin or event.symbol, event.ex_date, event.subject, event.exchange)


def _cross_feed_key(event: CorporateActionEvent) -> tuple:
    """Dedupe key when merging NSE + BSE rows for the same corporate action."""
    return (event.isin or event.symbol, event.ex_date, event.subject)


def fetch_nse_corporate_actions(
    symbol: str,
    *,
    start: date | None = None,
    end: date | None = None,
    client: ExchangeClient | None = None,
) -> list[CorporateActionEvent]:
    """Corporate actions for an NSE ``symbol`` over ``[start, end]``, oldest-first."""
    if not symbol:
        return []
    end = end or date.today()
    start = start or DEFAULT_EARLIEST

    owned = client is None
    if owned:
        client = warmed_nse_client()
    events: list[CorporateActionEvent] = []
    last_error: Exception | None = None
    headers = {**BROWSER_HEADERS, "Referer": _NSE_REFERER}
    try:
        for i, (w_start, w_end) in enumerate(_windows(start, end)):
            if i:
                _SLEEP(_CHUNK_SPACING)
            params = {
                "index": "equities",
                "symbol": symbol.upper(),
                "from_date": w_start.strftime("%d-%m-%Y"),
                "to_date": w_end.strftime("%d-%m-%Y"),
            }
            try:
                response = client.get(_NSE_PATH, params=params, headers=headers)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            if "application/json" not in response.headers.get("content-type", ""):
                last_error = CorporateActionFetchError(f"nse corp-actions {symbol}: non-JSON")
                continue
            payload = response.json()
            if not isinstance(payload, list):
                last_error = CorporateActionFetchError(
                    f"nse corp-actions {symbol}: unexpected shape"
                )
                continue
            for row in payload:
                if not isinstance(row, dict):
                    continue
                event = _nse_row_to_event(row)
                if event is not None:
                    events.append(event)
    finally:
        if owned:
            client.close()

    if not events and last_error is not None:
        msg = f"nse corp-actions {symbol}: {last_error}"
        raise CorporateActionFetchError(msg) from last_error

    seen: set[tuple] = set()
    deduped: list[CorporateActionEvent] = []
    for event in sorted(events, key=lambda e: (e.ex_date, e.subject)):
        key = _within_feed_key(event)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def lookup_bse_scripcode(symbol: str, *, client: ExchangeClient) -> str | None:
    """Resolve a BSE scrip code for ``symbol`` via PeerSmartSearch."""
    if not symbol:
        return None
    headers = {**BROWSER_HEADERS, "Referer": _BSE_REFERER}
    response = client.get(
        _BSE_LOOKUP_PATH,
        params={"Type": "SS", "text": symbol.upper()},
        headers=headers,
    )
    if response.status_code != 200:
        return None
    match = _BSE_SCRIP.search(response.text)
    return match.group(1) if match else None


def fetch_bse_corporate_actions(
    symbol: str,
    *,
    start: date | None = None,
    end: date | None = None,
    scripcode: str | None = None,
    client: ExchangeClient | None = None,
) -> list[CorporateActionEvent]:
    """Corporate actions for a BSE ``symbol`` (or ``scripcode``) over ``[start, end]``."""
    if not symbol and not scripcode:
        return []
    end = end or date.today()
    start = start or DEFAULT_EARLIEST

    owned = client is None
    if owned:
        client = warmed_bse_client()
    code = scripcode or lookup_bse_scripcode(symbol, client=client)
    if not code:
        if owned:
            client.close()
        return []

    events: list[CorporateActionEvent] = []
    last_error: Exception | None = None
    headers = {**BROWSER_HEADERS, "Referer": _BSE_REFERER}
    try:
        for i, (w_start, w_end) in enumerate(_windows(start, end)):
            if i:
                _SLEEP(_CHUNK_SPACING)
            params = {
                "ddlcategorys": "E",
                "ddlindustrys": "",
                "segment": "0",
                "strSearch": "D",
                "Fdate": w_start.strftime("%Y%m%d"),
                "TDate": w_end.strftime("%Y%m%d"),
                "scripcode": code,
            }
            try:
                response = client.get(_BSE_ACTIONS_PATH, params=params, headers=headers)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            if "application/json" not in response.headers.get("content-type", ""):
                last_error = CorporateActionFetchError(f"bse corp-actions {symbol}: non-JSON")
                continue
            payload = response.json()
            if not isinstance(payload, list):
                last_error = CorporateActionFetchError(
                    f"bse corp-actions {symbol}: unexpected shape"
                )
                continue
            for row in payload:
                if not isinstance(row, dict):
                    continue
                event = _bse_row_to_event(row)
                if event is not None:
                    events.append(event)
    finally:
        if owned:
            client.close()

    if not events and last_error is not None:
        msg = f"bse corp-actions {symbol}: {last_error}"
        raise CorporateActionFetchError(msg) from last_error

    seen: set[tuple] = set()
    deduped: list[CorporateActionEvent] = []
    for event in sorted(events, key=lambda e: (e.ex_date, e.subject)):
        key = _within_feed_key(event)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def fetch_corporate_actions(
    symbol: str,
    *,
    exchange: str = "",
    start: date | None = None,
    end: date | None = None,
    nse: ExchangeClient | None = None,
    bse: ExchangeClient | None = None,
) -> list[CorporateActionEvent]:
    """Fetch corporate actions for ``symbol``, routing by ``exchange``.

    ``exchange`` of ``BSE`` hits BSE only; ``NSE`` hits NSE only. A blank
    exchange fetches NSE first and merges any BSE-only rows, deduping on
    (isin|symbol, ex_date, subject) regardless of which feed supplied them.
    """
    ex = (exchange or "").strip().upper()
    if ex == "BSE":
        return fetch_bse_corporate_actions(symbol, start=start, end=end, client=bse)
    events = fetch_nse_corporate_actions(symbol, start=start, end=end, client=nse)
    if ex == "NSE":
        return events
    try:
        bse_events = fetch_bse_corporate_actions(symbol, start=start, end=end, client=bse)
    except CorporateActionFetchError:
        bse_events = []
    seen = {_cross_feed_key(e) for e in events}
    merged = list(events)
    for event in bse_events:
        key = _cross_feed_key(event)
        if key not in seen:
            seen.add(key)
            merged.append(event)
    merged.sort(key=lambda e: (e.ex_date, e.subject))
    return merged
