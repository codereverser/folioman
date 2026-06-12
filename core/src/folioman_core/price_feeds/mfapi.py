"""MF NAV feed via `mfapi.in <https://api.mfapi.in>`__.

Endpoints (GET, no auth, JSON):

- ``/mf/{amfi_code}``          — full history, newest-first
- ``/mf/{amfi_code}/latest``   — latest NAV only

Response shape::

    {
      "meta":   { "scheme_code": 122639, "scheme_name": "...", ... },
      "data":   [ {"date": "27-03-2025", "nav": "75.4123"}, ... ],
      "status": "SUCCESS"
    }

Dates are ``DD-MM-YYYY``; NAVs are decimal strings. The feed is well-behaved
but unofficial — callers should treat transient failures as recoverable
(later feeds add resilience patterns shared across feeds).
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from folioman_core.models.nav import NAVHistory, NAVPoint
from folioman_core.price_feeds.errors import NAVFetchError

BASE_URL = "https://api.mfapi.in"
DEFAULT_TIMEOUT = 30.0  # seconds — mfapi is normally fast; generous for cold connects

# mfapi is a free, unofficial feed — be a good citizen. Retry only *transient*
# failures (timeouts, connection drops, 429/5xx) with exponential backoff; a
# permanent error (404, bad JSON) fails fast without retrying. Indirected through
# ``_SLEEP`` so tests can stub the wait.
_MAX_RETRIES = 2  # i.e. up to 3 attempts
_BACKOFF_BASE = 0.5  # seconds; waits 0.5s, 1.0s, ...
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_SLEEP = time.sleep

__all__ = [
    "NAVFetchError",
    "fetch_latest_nav",
    "fetch_nav_history",
    "shared_client",
]


def shared_client() -> httpx.Client:
    """A client for a batch of calls — one pooled connection across schemes
    instead of a fresh TLS handshake per fund. Caller closes."""
    return httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT)


def _is_transient(exc: Exception) -> bool:
    """A failure worth retrying (vs a permanent 404 / malformed response)."""
    if isinstance(exc, httpx.TransportError):  # timeouts, connect/read errors
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    return False


def fetch_latest_nav(amfi_code: str, *, client: httpx.Client | None = None) -> NAVPoint | None:
    """Latest NAV for an AMFI scheme. Returns ``None`` when the scheme has no data."""
    payload = _fetch_json(f"/mf/{amfi_code}/latest", client=client)
    points = list(_parse_points(payload.get("data") or []))
    return points[0] if points else None


def fetch_nav_history(
    amfi_code: str,
    *,
    client: httpx.Client | None = None,
    since: date | None = None,
    until: date | None = None,
) -> NAVHistory:
    """Full NAV history for an AMFI scheme, oldest-first.

    Optional ``since``/``until`` bounds filter the returned series inclusively;
    apply them at the source so callers backfilling NAV history only
    pull the window they need.
    """
    payload = _fetch_json(f"/mf/{amfi_code}", client=client)
    meta = payload.get("meta") or {}
    points = sorted(_parse_points(payload.get("data") or []), key=lambda p: p.date)
    if since is not None:
        points = [p for p in points if p.date >= since]
    if until is not None:
        points = [p for p in points if p.date <= until]
    return NAVHistory(
        amfi_code=str(meta.get("scheme_code") or amfi_code),
        isin=str(meta.get("isin_growth") or meta.get("isin_div_reinvestment") or ""),
        points=points,
    )


def _fetch_json(
    path: str,
    *,
    client: httpx.Client | None,
    retries: int = _MAX_RETRIES,
    backoff: float = _BACKOFF_BASE,
) -> dict:
    """GET ``path`` and return the parsed JSON; wrap any failure as ``NAVFetchError``.

    Transient failures (timeouts, connection drops, 429/5xx) are retried with
    exponential backoff; permanent ones (404, malformed JSON) fail immediately.
    """
    owned = client is None
    if owned:
        client = httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT)
    try:
        for attempt in range(retries + 1):
            try:
                response = client.get(path)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt < retries and _is_transient(exc):
                    _SLEEP(backoff * (2**attempt))
                    continue
                msg = f"mfapi GET {path} failed: {exc}"
                raise NAVFetchError(msg) from exc
            if payload.get("status") != "SUCCESS":
                msg = f"mfapi GET {path}: status={payload.get('status', 'unknown')!r}"
                raise NAVFetchError(msg)
            return payload
        # Unreachable: the final attempt either returns or raises above.
        msg = f"mfapi GET {path}: exhausted retries"
        raise NAVFetchError(msg)
    finally:
        if owned:
            client.close()


def _parse_points(rows: list[dict]) -> Iterable[NAVPoint]:
    """Parse mfapi's ``{date: DD-MM-YYYY, nav: '...'}`` rows.

    Malformed rows are skipped silently — the public feed occasionally emits
    placeholders or partial entries; one bad row shouldn't drop the series.
    """
    for row in rows:
        raw_date = row.get("date")
        raw_nav = row.get("nav")
        if not raw_date or raw_nav is None:
            continue
        try:
            parsed_date = datetime.strptime(str(raw_date), "%d-%m-%Y").date()
            parsed_nav = Decimal(str(raw_nav))
        except (ValueError, InvalidOperation):
            continue
        yield NAVPoint(date=parsed_date, nav=parsed_nav)
