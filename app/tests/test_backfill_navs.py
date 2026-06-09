"""Historical NAV backfill. mfapi history is mocked — no live HTTP."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from folioman_app.models import NAVHistory, Security
from folioman_app.tasks.import_csv import create_manual_transaction
from folioman_app.tasks.refresh_navs import (
    backfill_missing_history,
    backfill_nav_history,
)
from folioman_core.models import NAVPoint, SecurityType
from folioman_core.price_feeds import mfapi

pytestmark = pytest.mark.django_db


def _history(*points: tuple[str, str]):
    return SimpleNamespace(
        points=[NAVPoint(date=dt.date.fromisoformat(d), nav=Decimal(nav)) for d, nav in points]
    )


def _mf_security() -> Security:
    return Security.objects.create(
        security_type=SecurityType.MF.value, name="Fund", amfi_code="122639"
    )


def test_backfill_writes_full_history(monkeypatch):
    monkeypatch.setattr(
        mfapi,
        "fetch_nav_history",
        lambda code, **_: _history(
            ("2024-01-01", "70"), ("2024-01-02", "71"), ("2024-01-03", "72")
        ),
    )
    sec = _mf_security()
    written = backfill_nav_history(sec)
    assert written == 3
    assert NAVHistory.objects.filter(security=sec).count() == 3
    assert NAVHistory.objects.get(security=sec, date=dt.date(2024, 1, 2)).nav == Decimal("71")


def test_backfill_is_idempotent(monkeypatch):
    monkeypatch.setattr(
        mfapi,
        "fetch_nav_history",
        lambda code, **_: _history(("2024-01-01", "70"), ("2024-01-02", "71")),
    )
    sec = _mf_security()
    assert backfill_nav_history(sec) == 2
    assert backfill_nav_history(sec) == 0  # nothing new on re-run
    assert NAVHistory.objects.filter(security=sec).count() == 2


def test_backfill_skips_non_mf(monkeypatch):
    monkeypatch.setattr(mfapi, "fetch_nav_history", lambda code, **_: _history(("2024-01-01", "1")))
    eq = Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
    )
    assert backfill_nav_history(eq) == 0
    assert NAVHistory.objects.count() == 0


def test_backfill_missing_bounds_since_earliest_transaction(monkeypatch, make_investor):
    captured = {}

    def _fake(code, *, since=None, **_):
        captured["since"] = since
        return _history(("2024-03-01", "80"))

    monkeypatch.setattr(mfapi, "fetch_nav_history", _fake)
    inv = make_investor()
    create_manual_transaction(
        inv,
        {
            "security_type": "mf",
            "name": "Fund",
            "amfi_code": "122639",
            "folio_number": "MF0001",
            "date": dt.date(2024, 3, 1),
            "transaction_type": "buy",
            "units": Decimal("100"),
            "price": Decimal("75"),
        },
    )
    summary = backfill_missing_history()
    assert captured["since"] == dt.date(2024, 3, 1)  # earliest transaction date
    assert summary["points"] == 1


def test_backfill_records_feed_errors(monkeypatch):
    def _boom(code, **_):
        raise mfapi.NAVFetchError("mfapi down")

    monkeypatch.setattr(mfapi, "fetch_nav_history", _boom)
    _mf_security()
    summary = backfill_missing_history()
    assert summary["errors"] == 1
    assert NAVHistory.objects.count() == 0


def test_backfill_navs_command(monkeypatch):
    monkeypatch.setattr(
        mfapi,
        "fetch_nav_history",
        lambda code, **_: _history(("2024-01-01", "70"), ("2024-01-02", "71")),
    )
    _mf_security()
    call_command("backfill_navs")
    assert NAVHistory.objects.count() == 2


# --- equity / quote-type history backfill (NSE-primary, Yahoo-fallback; mocked) ---


def _equity_security(symbol="RELIANCE", exchange="NSE") -> Security:
    return Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol=symbol,
        exchange=exchange,
    )


class _DummyClient:
    def close(self):
        pass


@pytest.fixture(autouse=True)
def _no_nse_warmup(monkeypatch):
    """backfill_missing_equity_history warms a real NSE session (cookie wall);
    stub it so the batch path never touches the network in tests."""
    from folioman_core.price_feeds import nse_history

    monkeypatch.setattr(nse_history, "warmed_client", lambda: _DummyClient())


def test_backfill_equity_uses_nse_first(monkeypatch):
    from folioman_app.tasks.refresh_navs import backfill_equity_history
    from folioman_core.price_feeds import nse_history, yfinance_feed

    captured = {}

    # Signature mirrors the real nse_history.fetch_history (start=, not since=).
    def _nse(symbol, *, start=None, end=None, client=None):
        captured["symbol"] = symbol
        return _history(("2024-01-01", "1400"), ("2024-01-02", "1410"))

    def _yahoo_forbidden(*a, **k):
        raise AssertionError("Yahoo must not be called when NSE returns data")

    monkeypatch.setattr(nse_history, "fetch_history", _nse)
    monkeypatch.setattr(yfinance_feed, "fetch_history", _yahoo_forbidden)
    sec = _equity_security()
    assert backfill_equity_history(sec) == 2
    assert captured["symbol"] == "RELIANCE"
    row = NAVHistory.objects.get(security=sec, date=dt.date(2024, 1, 2))
    assert row.nav == Decimal("1410")
    assert row.source == "nse"


def test_backfill_equity_falls_back_to_yahoo_when_nse_empty(monkeypatch):
    from folioman_app.tasks.refresh_navs import backfill_equity_history
    from folioman_core.price_feeds import nse_history, yfinance_feed

    monkeypatch.setattr(nse_history, "fetch_history", lambda *a, **k: _history())  # no points
    monkeypatch.setattr(
        yfinance_feed, "fetch_history", lambda *a, **k: _history(("2024-01-01", "99"))
    )
    sec = _equity_security()
    assert backfill_equity_history(sec) == 1
    assert NAVHistory.objects.get(security=sec).source == "yfinance"


def test_backfill_equity_falls_back_to_yahoo_when_nse_errors(monkeypatch):
    from folioman_app.tasks.refresh_navs import backfill_equity_history
    from folioman_core.price_feeds import nse_history, yfinance_feed
    from folioman_core.price_feeds.yfinance_feed import PriceFetchError

    def _boom(*a, **k):
        raise PriceFetchError("nse cookie wall")

    monkeypatch.setattr(nse_history, "fetch_history", _boom)
    monkeypatch.setattr(
        yfinance_feed, "fetch_history", lambda *a, **k: _history(("2024-01-01", "99"))
    )
    assert backfill_equity_history(_equity_security()) == 1


def test_backfill_equity_bse_only_skips_nse(monkeypatch):
    from folioman_app.tasks.refresh_navs import backfill_equity_history
    from folioman_core.price_feeds import nse_history, yfinance_feed

    def _nse_forbidden(*a, **k):
        raise AssertionError("NSE must not be queried for a BSE-only listing")

    monkeypatch.setattr(nse_history, "fetch_history", _nse_forbidden)
    monkeypatch.setattr(
        yfinance_feed, "fetch_history", lambda *a, **k: _history(("2024-01-01", "50"))
    )
    assert backfill_equity_history(_equity_security(exchange="BSE")) == 1


def test_backfill_equity_skips_symbolless(monkeypatch):
    from folioman_app.tasks.refresh_navs import backfill_equity_history
    from folioman_core.price_feeds import nse_history

    monkeypatch.setattr(nse_history, "fetch_history", lambda *a, **k: _history(("2024-01-01", "1")))
    sec = _equity_security(symbol="", exchange="")  # ISIN DB couldn't map a ticker
    assert backfill_equity_history(sec) == 0
    assert NAVHistory.objects.count() == 0


def test_backfill_equity_idempotent(monkeypatch):
    from folioman_app.tasks.refresh_navs import backfill_equity_history
    from folioman_core.price_feeds import nse_history

    monkeypatch.setattr(
        nse_history,
        "fetch_history",
        lambda *a, **k: _history(("2024-01-01", "1400"), ("2024-01-02", "1410")),
    )
    sec = _equity_security()
    assert backfill_equity_history(sec) == 2
    assert backfill_equity_history(sec) == 0
    assert NAVHistory.objects.filter(security=sec).count() == 2


def test_backfill_missing_equity_bounds_since_snapshot_when_no_transaction(
    monkeypatch, make_investor, make_holding
):
    """A snapshot-only equity (eCAS, no transactions) must backfill back to its
    snapshot as_of_date — else the value series is unpriced before it."""
    from folioman_app.tasks.refresh_navs import backfill_missing_equity_history
    from folioman_core.price_feeds import nse_history

    captured = {}

    def _nse(symbol, *, start=None, end=None, client=None):
        captured["start"] = start
        return _history(("2021-01-04", "500"))

    monkeypatch.setattr(nse_history, "fetch_history", _nse)
    inv = make_investor()
    sec = _equity_security()
    make_holding(
        investor=inv, security=sec, units=Decimal("9600"), as_of_date=dt.date(2020, 12, 31)
    )
    backfill_missing_equity_history()
    assert captured["start"] == dt.date(2020, 12, 31)
