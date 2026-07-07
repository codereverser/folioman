"""NAV history: the per-security fetchers plus the two batch mechanisms —
``extend_tails`` (head catch-up) and ``fill_gaps`` (interior + tail integrity sweep).
Feeds are mocked; no live HTTP."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from folioman_app.models import NAVHistory, Security
from folioman_app.services.trading_calendar import completed_trading_day
from folioman_app.tasks.import_csv import create_manual_transaction
from folioman_app.tasks.refresh_navs import backfill_nav_history, extend_tails, fill_gaps
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


def _mf_with_txn(code: str, *, on: dt.date, make_investor) -> Security:
    """An MF the batch mechanisms will consider — a transaction gives it a span start."""
    create_manual_transaction(
        make_investor(),
        {
            "security_type": "mf",
            "name": f"Fund {code}",
            "amfi_code": code,
            "folio_number": f"MF{code}",
            "date": on,
            "transaction_type": "buy",
            "units": Decimal("100"),
            "price": Decimal("10"),
        },
    )
    return Security.objects.get(amfi_code=code)


class _DummyClient:
    def close(self):
        pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Stub the batch feed warm-ups + bulk snapshots so the batch never touches the
    network. Bulk defaults to empty (→ per-scheme path); bulk tests override."""
    from folioman_core.price_feeds import amfi_bulk, nse_bhavcopy, nse_history

    monkeypatch.setattr(nse_history, "warmed_client", lambda: _DummyClient())
    monkeypatch.setattr(nse_bhavcopy, "warmed_client", lambda: _DummyClient())
    monkeypatch.setattr(nse_bhavcopy, "fetch_close_by_symbol", lambda *_a, **_k: {})
    monkeypatch.setattr(amfi_bulk, "fetch_range", lambda *_a, **_k: {})


# --- per-security fetchers (backfill_nav_history / backfill_equity_history) ---


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


# --- tail fetcher (extend_tails) ---


def test_extend_tails_bounds_since_earliest_transaction(monkeypatch, make_investor):
    """A fund with no stored NAV yet is filled from its first transaction."""
    captured = {}

    def _fake(code, *, since=None, **_):
        captured["since"] = since
        return _history(("2024-03-01", "80"))

    monkeypatch.setattr(mfapi, "fetch_nav_history", _fake)
    _mf_with_txn("122639", on=dt.date(2024, 3, 1), make_investor=make_investor)
    summary = extend_tails()
    assert captured["since"] == dt.date(2024, 3, 1)  # earliest transaction date
    assert summary["points"] == 1


def test_extend_tails_fetches_only_the_tail_not_full_history(monkeypatch, make_investor):
    """A deep-history fund that's merely behind at the head fetches from the day AFTER
    its last stored NAV — never re-pulling from the first transaction (the ALKEM case)."""
    captured = {}

    def _fake(code, *, since=None, **_):
        captured["since"] = since
        return _history()  # content irrelevant; we assert the fetch window

    monkeypatch.setattr(mfapi, "fetch_nav_history", _fake)
    latest = completed_trading_day(dt.date.today()) - dt.timedelta(days=5)
    sec = _mf_with_txn("122639", on=dt.date(2019, 1, 1), make_investor=make_investor)
    NAVHistory.objects.create(security=sec, date=dt.date(2019, 1, 1), nav=Decimal("10"))  # reaches
    NAVHistory.objects.create(security=sec, date=latest, nav=Decimal("20"))  # head, 5 days behind

    extend_tails()
    assert captured["since"] == latest + dt.timedelta(days=1)  # the tail, not 2019


def test_extend_tails_skips_a_head_current_fund(monkeypatch, make_investor):
    calls = {"n": 0}

    def _fake(code, *, since=None, **_):
        calls["n"] += 1
        return _history()

    monkeypatch.setattr(mfapi, "fetch_nav_history", _fake)
    sec = _mf_with_txn("122639", on=dt.date(2024, 1, 1), make_investor=make_investor)
    NAVHistory.objects.create(
        security=sec, date=completed_trading_day(dt.date.today()), nav=Decimal("30")
    )
    summary = extend_tails()
    assert calls["n"] == 0  # current to the last completed session → no fetch
    assert summary["skipped"] == 1


def test_extend_tails_records_feed_errors(monkeypatch, make_investor):
    def _boom(code, **_):
        raise mfapi.NAVFetchError("mfapi down")

    monkeypatch.setattr(mfapi, "fetch_nav_history", _boom)
    _mf_with_txn("122639", on=dt.date(2024, 1, 1), make_investor=make_investor)
    summary = extend_tails()
    assert summary["errors"] == 1
    assert NAVHistory.objects.count() == 0


def test_extend_tails_mf_short_lag_uses_one_amfi_range_report(monkeypatch, make_investor):
    """Several funds lagging only a short head gap catch up via one AMFI range-report
    call — the per-scheme feeds are never touched."""
    from folioman_core.price_feeds import amfi_bulk, captnemo

    end = completed_trading_day(dt.date.today())
    latest = end - dt.timedelta(days=3)  # a few days behind (well within 14)
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

    for code in ("100001", "100002"):
        sec = _mf_with_txn(code, on=since, make_investor=make_investor)
        NAVHistory.objects.create(security=sec, date=since, nav=Decimal("10"))
        NAVHistory.objects.create(security=sec, date=latest, nav=Decimal("10"))

    summary = extend_tails()
    assert summary["securities"] == 2
    for code, nav in (("100001", "11"), ("100002", "22")):
        row = NAVHistory.objects.get(security=Security.objects.get(amfi_code=code), date=end)
        assert row.nav == Decimal(nav)
        assert row.source == "amfi"


def test_extend_tails_single_fund_stays_per_scheme(monkeypatch, make_investor):
    """One lagging fund isn't worth a range report (one call either way) → per-scheme."""
    from folioman_core.price_feeds import amfi_bulk

    end = completed_trading_day(dt.date.today())
    latest = end - dt.timedelta(days=3)
    since = latest - dt.timedelta(days=30)

    def _range_forbidden(*_a, **_k):
        raise AssertionError("range report used for a single fund")

    monkeypatch.setattr(amfi_bulk, "fetch_range", _range_forbidden)
    monkeypatch.setattr(mfapi, "fetch_nav_history", lambda *a, **k: _history((str(end), "11")))

    sec = _mf_with_txn("100009", on=since, make_investor=make_investor)
    NAVHistory.objects.create(security=sec, date=since, nav=Decimal("10"))
    NAVHistory.objects.create(security=sec, date=latest, nav=Decimal("10"))

    summary = extend_tails()
    assert summary["securities"] == 1
    assert NAVHistory.objects.filter(security=sec, date=end).exists()


# --- gap filler (fill_gaps) ---


def test_fill_gaps_fills_an_interior_hole(monkeypatch, make_investor):
    """A hole between two stored points is detected and backfilled from the hole start
    — even though the head is current, which the tail fetcher would skip."""
    captured = {}

    def _fake(code, *, since=None, **_):
        captured["since"] = since
        return _history(("2024-01-02", "71"))

    monkeypatch.setattr(mfapi, "fetch_nav_history", _fake)
    end = completed_trading_day(dt.date.today())
    sec = _mf_with_txn("122639", on=dt.date(2024, 1, 1), make_investor=make_investor)
    NAVHistory.objects.create(security=sec, date=dt.date(2024, 1, 1), nav=Decimal("70"))
    NAVHistory.objects.create(security=sec, date=end, nav=Decimal("99"))  # head current

    fill_gaps()
    # 2024-01-02 (Tuesday) is the first missing trading day after the span start.
    assert captured["since"] == dt.date(2024, 1, 2)
    assert NAVHistory.objects.filter(security=sec, date=dt.date(2024, 1, 2)).exists()


def test_fill_gaps_repairs_deep_tail_behind_a_fresh_head(monkeypatch, make_investor):
    """Head current, but the series only reaches this year while the first trade is 2018
    — the gap filler fills from the first missing day (the 2018 trade date)."""
    captured = {}

    def _fake(code, *, since=None, **_):
        captured["since"] = since
        return _history(("2018-02-26", "200"))

    monkeypatch.setattr(mfapi, "fetch_nav_history", _fake)
    sec = _mf_with_txn("122639", on=dt.date(2018, 2, 26), make_investor=make_investor)
    NAVHistory.objects.create(
        security=sec, date=completed_trading_day(dt.date.today()), nav=Decimal("250")
    )
    fill_gaps()
    assert captured["since"] == dt.date(2018, 2, 26)  # from the first missing day


def test_fill_gaps_noop_on_contiguous_series(monkeypatch, make_investor):
    calls = {"n": 0}

    def _fake(code, *, since=None, **_):
        calls["n"] += 1
        return _history()

    monkeypatch.setattr(mfapi, "fetch_nav_history", _fake)
    end = completed_trading_day(dt.date.today())
    sec = _mf_with_txn("122639", on=end, make_investor=make_investor)
    # A single-day span with that day stored is contiguous → nothing to fill.
    NAVHistory.objects.create(security=sec, date=end, nav=Decimal("50"))
    summary = fill_gaps()
    assert calls["n"] == 0
    assert summary["skipped"] >= 1


def test_backfill_navs_command_runs_the_gap_filler(monkeypatch, make_investor):
    monkeypatch.setattr(
        mfapi,
        "fetch_nav_history",
        lambda code, **_: _history(("2024-01-01", "70"), ("2024-01-02", "71")),
    )
    _mf_with_txn("122639", on=dt.date(2024, 1, 1), make_investor=make_investor)
    call_command("backfill_navs")
    sec = Security.objects.get(amfi_code="122639")
    assert NAVHistory.objects.filter(security=sec).count() == 2


# --- equity / quote-type per-security fetcher (NSE-primary, Yahoo-fallback) ---


def _equity_security(symbol="RELIANCE", exchange="NSE") -> Security:
    return Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol=symbol,
        exchange=exchange,
    )


def test_backfill_equity_uses_nse_first(monkeypatch):
    from folioman_app.tasks.refresh_navs import backfill_equity_history
    from folioman_core.price_feeds import nse_history, yfinance_feed

    captured = {}

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


def test_extend_tails_equity_bounds_since_snapshot_when_no_transaction(
    monkeypatch, make_investor, make_holding
):
    """A snapshot-only equity (eCAS, no transactions) fills from its snapshot as_of_date."""
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
    extend_tails()
    assert captured["start"] == dt.date(2020, 12, 31)


# --- the per-symbol-vs-bulk cost switch (bhavcopy) ---


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


def test_extend_tails_equity_bulk_scatters_one_bhavcopy_across_symbols(
    monkeypatch, make_investor, make_holding
):
    """When bulk is cheaper, each day's bhavcopy is fetched once and scattered to every
    symbol that needs it — no per-symbol history calls."""
    import folioman_app.tasks.refresh_navs as rn
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

    summary = extend_tails()

    assert calls["n"] >= 1
    assert summary["securities"] == 2
    assert NAVHistory.objects.filter(security=rel, source="nse-bhavcopy").exists()
    assert NAVHistory.objects.filter(security=infy, source="nse-bhavcopy").exists()


def test_extend_tails_equity_bulk_leaves_uncovered_symbol_to_per_symbol(
    monkeypatch, make_investor, make_holding
):
    """A symbol absent from the bhavcopy (bond / suspended) isn't marked handled, so it
    still backfills via the per-symbol feed."""
    import folioman_app.tasks.refresh_navs as rn
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

    extend_tails()

    assert NAVHistory.objects.filter(security=rel, source="nse-bhavcopy").exists()  # bulk
    assert seen["symbol"] == "INFY"  # the uncovered symbol fell through to per-symbol
    assert NAVHistory.objects.filter(security=infy, source="nse").exists()
