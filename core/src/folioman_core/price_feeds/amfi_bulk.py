"""AMFI bulk NAV feed — every scheme's latest NAV in one file.

AMFI publishes the whole market's current NAV as a single ``;``-delimited text
file, refreshed each business day. One GET replaces a per-scheme ``/latest`` call
in the daily refresh — the difference between one request and thousands.

Format (one header line, bare AMC/scheme-type names as section headers between
blocks, then data rows)::

    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date
    120503;INF209K01157;INF209K01165;Aditya Birla ...;123.4567;01-Jul-2026

Non-data lines (the header, blanks, bare section names) have fewer than five
``;`` and are skipped. A row whose NAV is non-numeric ("N.A.") is skipped.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from folioman_core.models.nav import NAVPoint
from folioman_core.price_feeds.errors import NAVFetchError

# The portal host serves NAVAll.txt reliably; the www host is frequently
# unreachable (connection timeouts), so it isn't used.
BASE_URL = "https://portal.amfiindia.com"
_NAVALL_PATH = "/spages/NAVAll.txt"
# Historical NAV report — all schemes across a date range in one file (~0.9 MB/day).
_HISTORY_PATH = "/DownloadNAVHistoryReport_Po.aspx"
DEFAULT_TIMEOUT = 60.0  # the file is a few MB — generous for a cold connect

# Free public feed: retry only transient failures (timeouts, 429/5xx), fail fast
# on the rest. Indirected through ``_SLEEP`` so tests stub the wait.
_MAX_RETRIES = 2
_BACKOFF_BASE = 0.5
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_SLEEP = time.sleep

__all__ = [
    "NAVFetchError",
    "fetch_all_latest",
    "fetch_range",
    "parse_nav_history",
    "parse_navall",
    "shared_client",
]


def shared_client() -> httpx.Client:
    """A client for the single bulk call. Caller closes."""
    return httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    return False


def fetch_all_latest(*, client: httpx.Client | None = None) -> dict[str, NAVPoint]:
    """Latest NAV for every scheme, keyed by AMFI code **and** each ISIN.

    A scheme's numeric AMFI code and its payout/growth/reinvest ISINs all map to
    the same point, so a caller looks it up by whichever identifier it stores.
    """
    return parse_navall(_fetch_text(_NAVALL_PATH, client=client))


def parse_navall(text: str) -> dict[str, NAVPoint]:
    """Parse NAVAll.txt into ``{amfi_code | isin: NAVPoint}``."""
    out: dict[str, NAVPoint] = {}
    for line in text.splitlines():
        if line.count(";") < 5:  # header, blank, or a bare AMC/scheme-type name
            continue
        code, isin_a, isin_b, _name, raw_nav, raw_date = line.split(";")[:6]
        try:
            nav = Decimal(raw_nav.strip())
            when = datetime.strptime(raw_date.strip(), "%d-%b-%Y").date()
        except (ValueError, InvalidOperation):
            continue
        point = NAVPoint(date=when, nav=nav)
        for key in (code.strip(), isin_a.strip(), isin_b.strip()):
            if key and key != "-":
                out.setdefault(key, point)
    return out


def fetch_range(
    frmdt: date, todt: date, *, client: httpx.Client | None = None
) -> dict[str, list[NAVPoint]]:
    """Every scheme's NAVs across ``[frmdt, todt]``, keyed by AMFI code and each ISIN.

    One request backfills a whole date-range gap for the entire MF universe, instead
    of a per-scheme call each. The report caps at ~90 days per request; callers keep
    the window small (a fortnight-ish catch-up) since the file grows ~0.9 MB/day.
    """
    path = f"{_HISTORY_PATH}?frmdt={frmdt:%d-%b-%Y}&todt={todt:%d-%b-%Y}"
    return parse_nav_history(_fetch_text(path, client=client))


def parse_nav_history(text: str) -> dict[str, list[NAVPoint]]:
    """Parse the NAV history report into ``{amfi_code | isin: [NAVPoint, ...]}``.

    Columns differ from NAVAll.txt (name comes second, ISINs third/fourth):
    ``Scheme Code;Scheme Name;ISIN Div Payout/Growth;ISIN Div Reinvestment;NAV;
    Repurchase;Sale;Date``. Points accumulate per key across the requested dates.
    """
    out: dict[str, list[NAVPoint]] = defaultdict(list)
    for line in text.splitlines():
        if line.count(";") < 7:  # header, blank, or a bare AMC/scheme-type name
            continue
        parts = line.split(";")
        code, _name, isin_a, isin_b, raw_nav, _repurchase, _sale, raw_date = parts[:8]
        try:
            nav = Decimal(raw_nav.strip())
            when = datetime.strptime(raw_date.strip(), "%d-%b-%Y").date()
        except (ValueError, InvalidOperation):
            continue
        point = NAVPoint(date=when, nav=nav)
        for key in (code.strip(), isin_a.strip(), isin_b.strip()):
            if key and key != "-":
                out[key].append(point)
    return dict(out)


def _fetch_text(
    path: str,
    *,
    client: httpx.Client | None,
    retries: int = _MAX_RETRIES,
    backoff: float = _BACKOFF_BASE,
) -> str:
    """GET ``path`` as text; wrap any failure as ``NAVFetchError`` (transient retried)."""
    owned = client is None
    if owned:
        client = shared_client()
    try:
        for attempt in range(retries + 1):
            try:
                response = client.get(path)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                if attempt < retries and _is_transient(exc):
                    _SLEEP(backoff * (2**attempt))
                    continue
                msg = f"amfi GET {path} failed: {exc}"
                raise NAVFetchError(msg) from exc
            if not response.text or ";" not in response.text:
                msg = f"amfi GET {path}: unexpected body"
                raise NAVFetchError(msg)
            return response.text
        msg = f"amfi GET {path}: exhausted retries"  # unreachable
        raise NAVFetchError(msg)
    finally:
        if owned:
            client.close()
