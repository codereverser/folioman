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

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from folioman_core.models.nav import NAVHistory, NAVPoint

BASE_URL = "https://api.mfapi.in"
DEFAULT_TIMEOUT = 30.0  # seconds — mfapi is normally fast; generous for cold connects


class NAVFetchError(RuntimeError):
    """mfapi returned an error, or the response was unusable."""


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


def _fetch_json(path: str, *, client: httpx.Client | None) -> dict:
    """GET ``path`` and return the parsed JSON; wrap any failure as ``NAVFetchError``."""
    owned = client is None
    if owned:
        client = httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT)
    try:
        response = client.get(path)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        msg = f"mfapi GET {path} failed: {exc}"
        raise NAVFetchError(msg) from exc
    finally:
        if owned:
            client.close()
    if payload.get("status") != "SUCCESS":
        msg = f"mfapi GET {path}: status={payload.get('status', 'unknown')!r}"
        raise NAVFetchError(msg)
    return payload


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
