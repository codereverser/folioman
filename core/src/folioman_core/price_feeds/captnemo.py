"""MF NAV feed via `captnemo <https://mf.captnemo.in>`__, keyed by ISIN.

Endpoint (GET, no auth, JSON):

- ``/nav/{isin}`` — full history plus the latest point

Response shape::

    {
      "ISIN":  "INF179K01608",
      "name":  "HDFC Flexi Cap Fund - Growth Plan",
      "nav":   1924.081,
      "date":  "2026-06-11",
      "historical_nav": [ ["2006-04-03", 130.819], ..., ["2026-06-11", 1924.081] ]
    }

Both AMFI-derived, so the data matches mfapi point-for-point — this is a second
mirror of the same source, keyed by ISIN instead of AMFI code. Differences that
matter to a caller:

- Dates are ISO ``YYYY-MM-DD`` (mfapi uses ``DD-MM-YYYY``).
- ``historical_nav`` is oldest-first already (mfapi is newest-first).
- NAVs arrive as JSON **floats**, not strings — parsed via ``Decimal(str(...))``
  so the shortest round-trip repr lands in the Decimal cleanly (``Decimal(float)``
  would inject binary-float noise).
- A missing ISIN is a clean ``404``; there is no ``status`` envelope.

The feed refreshes once daily, so its newest point can trail the live NAV by up
to a trading day — the daily ``refresh_navs`` pass (mfapi ``/latest``) fills that
gap, and the backfill freshness grace absorbs the lag without re-pulling.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from folioman_core.models.nav import NAVHistory, NAVPoint
from folioman_core.price_feeds.errors import NAVFetchError

BASE_URL = "https://mf.captnemo.in"
DEFAULT_TIMEOUT = 30.0  # seconds — edge-cached and fast; generous for cold connects

# Same good-citizen retry policy as the mfapi mirror: retry only *transient*
# failures (timeouts, connection drops, 429/5xx) with exponential backoff; a
# permanent error (404 unknown ISIN, bad JSON) fails fast. ``_SLEEP`` is
# indirected so tests can stub the wait.
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


def fetch_latest_nav(isin: str, *, client: httpx.Client | None = None) -> NAVPoint | None:
    """Latest NAV for an ISIN. Returns ``None`` when the scheme has no data.

    captnemo always returns the full series; the top-level ``date``/``nav`` is the
    newest point, so use it directly rather than walking ``historical_nav``.
    """
    payload = _fetch_json(f"/nav/{isin}", client=client)
    points = list(_parse_points([[payload.get("date"), payload.get("nav")]]))
    return points[0] if points else None


def fetch_nav_history(
    isin: str,
    *,
    client: httpx.Client | None = None,
    since: date | None = None,
    until: date | None = None,
) -> NAVHistory:
    """Full NAV history for an ISIN, oldest-first.

    Optional ``since``/``until`` bounds filter the returned series inclusively;
    apply them at the source so callers backfilling NAV history only pull the
    window they need.
    """
    payload = _fetch_json(f"/nav/{isin}", client=client)
    points = sorted(_parse_points(payload.get("historical_nav") or []), key=lambda p: p.date)
    if since is not None:
        points = [p for p in points if p.date >= since]
    if until is not None:
        points = [p for p in points if p.date <= until]
    return NAVHistory(isin=str(payload.get("ISIN") or isin), points=points)


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
                msg = f"captnemo GET {path} failed: {exc}"
                raise NAVFetchError(msg) from exc
            if not isinstance(payload, dict) or "historical_nav" not in payload:
                # captnemo has no status envelope; an unexpected body (e.g. an
                # error JSON, or HTML) is unusable. Treat as permanent.
                msg = f"captnemo GET {path}: unexpected response shape"
                raise NAVFetchError(msg)
            return payload
        # Unreachable: the final attempt either returns or raises above.
        msg = f"captnemo GET {path}: exhausted retries"
        raise NAVFetchError(msg)
    finally:
        if owned:
            client.close()


def _parse_points(rows: list[list]) -> Iterable[NAVPoint]:
    """Parse captnemo's ``[date: YYYY-MM-DD, nav: float]`` pairs.

    NAVs come as JSON floats — ``Decimal(str(value))`` routes through the float's
    shortest round-trip repr so the Decimal is exact (``Decimal(value)`` would
    carry binary-float noise). Malformed rows are skipped silently — one bad row
    shouldn't drop the series.
    """
    for row in rows:
        if not row or len(row) < 2:
            continue
        raw_date, raw_nav = row[0], row[1]
        if not raw_date or raw_nav is None:
            continue
        try:
            parsed_date = datetime.strptime(str(raw_date), "%Y-%m-%d").date()
            parsed_nav = Decimal(str(raw_nav))
        except (ValueError, InvalidOperation):
            continue
        yield NAVPoint(date=parsed_date, nav=parsed_nav)
