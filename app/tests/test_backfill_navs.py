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
        points=[NAVPoint(date=dt.date.fromisoformat(d), nav=Decimal(nav)) for d, nav in points],
        isin="",
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


def test_backfill_refills_short_tail_despite_fresh_head(monkeypatch, make_investor):
    """A series current at the head but not reaching the first trade must re-pull —
    the head-only freshness check used to skip it, leaving early history unpriced."""
    from folioman_app.models import NAVHistory
    from folioman_app.services.trading_calendar import last_trading_day

    captured = {}

    def _fake(code, *, since=None, **_):
        captured["since"] = since
        return _history(("2018-02-26", "200"))

    monkeypatch.setattr(mfapi, "fetch_nav_history", _fake)
    inv = make_investor()
    create_manual_transaction(
        inv,
        {
            "security_type": "mf",
            "name": "Fund",
            "amfi_code": "122639",
            "folio_number": "MF0001",
            "date": dt.date(2018, 2, 26),
            "transaction_type": "buy",
            "units": Decimal("100"),
            "price": Decimal("200"),
        },
    )
    sec = Security.objects.get(amfi_code="122639")
    # Head is current (today's close) but the tail only reaches this year — a shallow
    # series that the old head-only check wrongly treated as fully fresh.
    NAVHistory.objects.create(
        security=sec, date=last_trading_day(dt.date.today()), nav=Decimal("250")
    )
    backfill_missing_history()
    assert captured["since"] == dt.date(2018, 2, 26)  # re-pulled from the first trade


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


def test_backfill_mf_short_lag_uses_one_amfi_range_report(monkeypatch, make_investor):
    """Several funds lagging only a short head gap catch up via one AMFI range-report
    call — the per-scheme feeds are never touched."""
    from folioman_app.services.trading_calendar import completed_trading_day
    from folioman_app.tasks.refresh_navs import backfill_missing_history
    from folioman_core.price_feeds import amfi_bulk, captnemo, mfapi

    end = completed_trading_day(dt.date.today())  # bulk fills up to the last completed day
    latest = end - dt.timedelta(days=3)  # each fund a few days behind (well within 14)
    since = latest - dt.timedelta(days=30)

    def _forbidden(*_a, **_k):
        raise AssertionError("per-scheme MF feed hit despite the AMFI range report")

    monkeypatch.setattr(mfapi, "fetch_nav_history", _forbidden)
    monkeypatch.setattr(captnemo, "fetch_nav_history", _forbidden)
    monkeypatch.setattr(
        amfi_bulk,
        "fetch_range",
        lambda frmdt, todt, **_: {
            "100001": [NAVPoint(date=end, nav=Decimal("11"))],
            "100002": [NAVPoint(date=end, nav=Decimal("22"))],
        },
    )

    inv = make_investor()
    for code in ("100001", "100002"):
        create_manual_transaction(
            inv,
            {
                "security_type": "mf",
                "name": f"Fund {code}",
                "amfi_code": code,
                "folio_number": f"MF{code}",
                "date": since,
                "transaction_type": "buy",
                "units": Decimal("100"),
                "price": Decimal("10"),
            },
        )
        sec = Security.objects.get(amfi_code=code)
        NAVHistory.objects.create(security=sec, date=since, nav=Decimal("10"))  # earliest≈since
        NAVHistory.objects.create(security=sec, date=latest, nav=Decimal("10"))  # short head lag

    summary = backfill_missing_history()

    assert summary["securities"] == 2
    for code, nav in (("100001", "11"), ("100002", "22")):
        sec = Security.objects.get(amfi_code=code)
        row = NAVHistory.objects.get(security=sec, date=end)
        assert row.nav == Decimal(nav)
        assert row.source == "amfi"


def test_backfill_mf_single_fund_stays_per_scheme(monkeypatch, make_investor):
    """One lagging fund isn't worth a range report (one call either way) → per-scheme."""
    from folioman_app.services.trading_calendar import completed_trading_day
    from folioman_app.tasks.refresh_navs import backfill_missing_history
    from folioman_core.price_feeds import amfi_bulk, mfapi

    end = completed_trading_day(dt.date.today())
    latest = end - dt.timedelta(days=3)
    since = latest - dt.timedelta(days=30)

    def _range_forbidden(*_a, **_k):
        raise AssertionError("range report used for a single fund")

    monkeypatch.setattr(amfi_bulk, "fetch_range", _range_forbidden)
    # Code-only fund → per-scheme backfill goes through mfapi (captnemo is ISIN-keyed).
    monkeypatch.setattr(mfapi, "fetch_nav_history", lambda *a, **k: _history((str(end), "11")))

    inv = make_investor()
    create_manual_transaction(
        inv,
        {
            "security_type": "mf",
            "name": "Solo Fund",
            "amfi_code": "100009",
            "folio_number": "MF100009",
            "date": since,
            "transaction_type": "buy",
            "units": Decimal("100"),
            "price": Decimal("10"),
        },
    )
    sec = Security.objects.get(amfi_code="100009")
    NAVHistory.objects.create(security=sec, date=since, nav=Decimal("10"))
    NAVHistory.objects.create(security=sec, date=latest, nav=Decimal("10"))

    summary = backfill_missing_history()
    assert summary["securities"] == 1
    assert NAVHistory.objects.filter(security=sec, date=end).exists()


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
    """The equity batch warms a real NSE session (cookie wall) and, when the bulk
    switch fires, downloads bhavcopies — stub both so the batch never touches the
    network. Bhavcopy defaults to empty (bulk covers nothing → per-symbol path);
    bulk-path tests override ``fetch_close_by_symbol`` with data."""
    from folioman_core.price_feeds import amfi_bulk, nse_bhavcopy, nse_history

    monkeypatch.setattr(nse_history, "warmed_client", lambda: _DummyClient())
    monkeypatch.setattr(nse_bhavcopy, "warmed_client", lambda: _DummyClient())
    monkeypatch.setattr(nse_bhavcopy, "fetch_close_by_symbol", lambda *_a, **_k: {})
    # MF bulk range report defaults to empty (bulk covers nothing → per-scheme path);
    # the MF-bulk test overrides this with data.
    monkeypatch.setattr(amfi_bulk, "fetch_range", lambda *_a, **_k: {})


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


# --- bhavcopy bulk backfill + the per-symbol-vs-bulk cost switch ---


def test_prefer_bulk_true_when_span_shallow_and_many_symbols():
    from folioman_app.tasks.refresh_navs import _prefer_bulk

    cutoff = dt.date(2026, 7, 1)  # Wednesday
    since = dt.date(2026, 6, 29)  # Monday → span is 3 trading days
    many = [(None, since, None, True) for _ in range(5)]
    assert _prefer_bulk(many, cutoff) is True  # 3 bhavcopy files < 5 per-symbol pulls


def test_prefer_bulk_false_when_any_symbol_needs_deep_history():
    from folioman_app.tasks.refresh_navs import _prefer_bulk

    cutoff = dt.date(2026, 7, 1)
    deep = [(None, dt.date(2018, 1, 1), None, True), (None, dt.date(2019, 1, 1), None, True)]
    assert _prefer_bulk(deep, cutoff) is False  # ~2000 bhavcopy files ≫ ~17 chunked pulls


def _nse_equity(*, name, isin, symbol) -> Security:
    return Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name=name,
        isin=isin,
        symbol=symbol,
        exchange="NSE",
    )


def test_backfill_equity_bulk_scatters_one_bhavcopy_across_symbols(
    monkeypatch, make_investor, make_holding
):
    """When bulk is cheaper, each day's bhavcopy is fetched once and its closes
    scattered to every symbol that needs it — no per-symbol history calls."""
    import folioman_app.tasks.refresh_navs as rn
    from folioman_app.tasks.refresh_navs import backfill_missing_equity_history
    from folioman_core.price_feeds import nse_bhavcopy, nse_history

    monkeypatch.setattr(rn, "_prefer_bulk", lambda *_a, **_k: True)

    calls = {"n": 0}

    def _bhav(on, *, client=None):
        calls["n"] += 1
        return {
            "RELIANCE": NAVPoint(date=on, nav=Decimal("1400")),
            "INFY": NAVPoint(date=on, nav=Decimal("1550")),
        }

    monkeypatch.setattr(nse_bhavcopy, "fetch_close_by_symbol", _bhav)

    def _forbidden(*_a, **_k):
        raise AssertionError("per-symbol NSE history hit despite bhavcopy bulk")

    monkeypatch.setattr(nse_history, "fetch_history", _forbidden)

    inv = make_investor()
    rel = _nse_equity(name="Reliance", isin="INE002A01018", symbol="RELIANCE")
    infy = _nse_equity(name="Infosys", isin="INE009A01021", symbol="INFY")
    recent = dt.date.today() - dt.timedelta(days=7)
    make_holding(investor=inv, security=rel, units=Decimal("10"), as_of_date=recent)
    make_holding(investor=inv, security=infy, units=Decimal("20"), as_of_date=recent)

    summary = backfill_missing_equity_history()

    assert calls["n"] >= 1
    assert summary["securities"] == 2
    assert NAVHistory.objects.filter(security=rel, source="nse-bhavcopy").exists()
    assert NAVHistory.objects.filter(security=infy, source="nse-bhavcopy").exists()


def test_backfill_equity_bulk_leaves_uncovered_symbol_to_per_symbol(
    monkeypatch, make_investor, make_holding
):
    """A symbol absent from the bhavcopy (bond / suspended / not-yet-listed) isn't
    marked handled, so it still backfills via the per-symbol feed."""
    import folioman_app.tasks.refresh_navs as rn
    from folioman_app.tasks.refresh_navs import backfill_missing_equity_history
    from folioman_core.price_feeds import nse_bhavcopy, nse_history

    monkeypatch.setattr(rn, "_prefer_bulk", lambda *_a, **_k: True)
    monkeypatch.setattr(
        nse_bhavcopy,
        "fetch_close_by_symbol",
        lambda on, **_: {"RELIANCE": NAVPoint(date=on, nav=Decimal("1400"))},  # INFY absent
    )

    seen = {}

    def _nse(symbol, *, start=None, end=None, client=None):
        seen["symbol"] = symbol
        return _history((str(dt.date.today() - dt.timedelta(days=2)), "1550"))

    monkeypatch.setattr(nse_history, "fetch_history", _nse)

    inv = make_investor()
    rel = _nse_equity(name="Reliance", isin="INE002A01018", symbol="RELIANCE")
    infy = _nse_equity(name="Infosys", isin="INE009A01021", symbol="INFY")
    recent = dt.date.today() - dt.timedelta(days=7)
    make_holding(investor=inv, security=rel, units=Decimal("10"), as_of_date=recent)
    make_holding(investor=inv, security=infy, units=Decimal("20"), as_of_date=recent)

    backfill_missing_equity_history()

    assert NAVHistory.objects.filter(security=rel, source="nse-bhavcopy").exists()  # bulk
    assert seen["symbol"] == "INFY"  # the uncovered symbol fell through to per-symbol
    assert NAVHistory.objects.filter(security=infy, source="nse").exists()
