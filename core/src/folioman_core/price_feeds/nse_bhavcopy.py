"""NSE cash-market bhavcopy — every equity's close for one trading day, in one file.

Replaces a per-symbol history call in the daily refresh: a single zipped CSV holds
the whole market's OHLC for the day. Two layouts are tried — the current UDiFF
bhavcopy (zipped, ISO-20022-style columns), then the legacy full bhavdata CSV NSE
served before the 2024 switch.

The files live on the archives host (``nsearchives.nseindia.com``), not the API
host, but share the ``.nseindia.com`` cookie domain — so a warmed
:class:`ExchangeClient` reaches them. A missing file (weekend / not-yet-published
/ layout change) yields ``{}`` so the caller falls back to per-symbol quotes.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from folioman_core.models.nav import NAVPoint
from folioman_core.price_feeds.nse_bse_client import ExchangeClient, nse_client

_ARCHIVES = "https://nsearchives.nseindia.com"
_REFERER = "https://www.nseindia.com/all-reports"
# Series that carry an ordinary equity close (rolling-settlement + when-issued).
_EQUITY_SERIES = frozenset({"EQ", "BE", "BZ", "SM", "ST"})

__all__ = ["fetch_close_by_symbol", "parse_bhavcopy_csv", "parse_legacy_csv", "warmed_client"]


def warmed_client() -> ExchangeClient:
    """A cookie-warmed NSE client (shared with the other NSE feeds)."""
    return nse_client()


def _udiff_url(on: date) -> str:
    return f"{_ARCHIVES}/content/cm/BhavCopy_NSE_CM_0_0_0_{on:%Y%m%d}_F_0000.csv.zip"


def _legacy_url(on: date) -> str:
    return f"{_ARCHIVES}/products/content/sec_bhavdata_full_{on:%d%m%Y}.csv"


def fetch_close_by_symbol(on: date, *, client: ExchangeClient | None = None) -> dict[str, NAVPoint]:
    """Every equity's close for ``on``, keyed by NSE symbol. ``{}`` when unavailable.

    Best-effort by design: any download/parse failure returns ``{}`` so the daily
    refresh degrades to per-symbol quotes rather than dropping the day.
    """
    owned = client is None
    if owned:
        client = warmed_client()
    try:
        text = _download_udiff(on, client)
        if text is not None:
            return parse_bhavcopy_csv(text, on)
        text = _download_legacy(on, client)
        if text is not None:
            return parse_legacy_csv(text, on)
    finally:
        if owned:
            client.close()
    return {}


def _download_udiff(on: date, client: ExchangeClient) -> str | None:
    """The current zipped UDiFF bhavcopy CSV, or ``None`` if unavailable/not a zip."""
    try:
        resp = client.get(_udiff_url(on), headers={"Referer": _REFERER})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200 or not resp.content:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
            if name is None:
                return None
            return zf.read(name).decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        return None


def _download_legacy(on: date, client: ExchangeClient) -> str | None:
    """The pre-2024 plain-CSV bhavdata file, or ``None`` if unavailable."""
    try:
        resp = client.get(_legacy_url(on), headers={"Referer": _REFERER})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200 or "," not in resp.text:
        return None
    return resp.text


def _to_point(raw_close: str, on: date) -> NAVPoint | None:
    try:
        return NAVPoint(date=on, nav=Decimal(raw_close.replace(",", "").strip()))
    except (ValueError, InvalidOperation):
        return None


def _by_symbol(text: str, on: date, *, symbol_col: str, series_col: str, close_col: str) -> dict:
    out: dict[str, NAVPoint] = {}
    for raw in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        if row.get(series_col) not in _EQUITY_SERIES:
            continue
        symbol = row.get(symbol_col, "")
        point = _to_point(row.get(close_col, ""), on)
        if symbol and point:
            out[symbol.upper()] = point
    return out


def parse_bhavcopy_csv(text: str, on: date) -> dict[str, NAVPoint]:
    """Parse the UDiFF bhavcopy (``TckrSymb`` / ``SctySrs`` / ``ClsPric``)."""
    return _by_symbol(text, on, symbol_col="TckrSymb", series_col="SctySrs", close_col="ClsPric")


def parse_legacy_csv(text: str, on: date) -> dict[str, NAVPoint]:
    """Parse the legacy full bhavdata (``SYMBOL`` / ``SERIES`` / ``CLOSE_PRICE``)."""
    return _by_symbol(text, on, symbol_col="SYMBOL", series_col="SERIES", close_col="CLOSE_PRICE")
