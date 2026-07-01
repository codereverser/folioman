"""NAV refresh + management commands. Feeds are mocked — no live HTTP."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from folioman_app.models import NAVHistory, Security
from folioman_app.services.trading_calendar import last_trading_day
from folioman_app.tasks import refresh_navs as refresh_navs_mod
from folioman_app.tasks.import_csv import create_manual_transaction
from folioman_app.tasks.refresh_navs import backfill_missing_history, refresh_navs
from folioman_core.models import NAVPoint, Quote, SecurityType
from folioman_core.price_feeds import captnemo, coingecko, mfapi, nse_history, yfinance_feed
from folioman_core.price_feeds.yfinance_feed import PriceFetchError

pytestmark = pytest.mark.django_db

_TODAY = dt.date(2025, 6, 2)


@pytest.fixture(autouse=True)
def _no_request_spacing(monkeypatch):
    """Don't actually sleep between feed calls in tests."""
    monkeypatch.setattr(refresh_navs_mod, "_SLEEP", lambda *_a, **_k: None)


class _DummyClient:
    def close(self):
        pass


@pytest.fixture(autouse=True)
def _no_bulk_by_default(monkeypatch):
    """Default the whole-market snapshots to empty so existing tests exercise the
    per-security path and never touch the network. Bulk-path tests override these."""
    monkeypatch.setattr(refresh_navs_mod.amfi_bulk, "fetch_all_latest", lambda **_: {})
    monkeypatch.setattr(refresh_navs_mod.nse_bhavcopy, "warmed_client", lambda: _DummyClient())
    monkeypatch.setattr(
        refresh_navs_mod.nse_bhavcopy, "fetch_close_by_symbol", lambda *_a, **_k: {}
    )


@pytest.fixture
def mocked_feeds(monkeypatch):
    monkeypatch.setattr(
        mfapi, "fetch_latest_nav", lambda code, **_: NAVPoint(date=_TODAY, nav=Decimal("75.5"))
    )
    monkeypatch.setattr(
        yfinance_feed,
        "fetch_quote",
        lambda symbol, **_: Quote(
            as_of=_TODAY, price=Decimal("2850"), currency="INR", source="yfinance"
        ),
    )
    monkeypatch.setattr(
        coingecko,
        "fetch_quote",
        lambda coin_id, **_: Quote(
            as_of=_TODAY, price=Decimal("8500000"), currency="INR", source="coingecko"
        ),
    )


def test_refresh_writes_navhistory_per_type(mocked_feeds):
    mf = Security.objects.create(
        security_type=SecurityType.MF.value, name="Fund", amfi_code="122639"
    )
    eq = Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
    )
    crypto = Security.objects.create(
        security_type=SecurityType.CRYPTO.value,
        name="Bitcoin",
        symbol="BTC",
        metadata={"coin_id": "bitcoin"},
    )

    summary = refresh_navs()
    assert summary["updated"] == 3
    assert NAVHistory.objects.get(security=mf, date=_TODAY).nav == Decimal("75.5")
    assert NAVHistory.objects.get(security=eq, date=_TODAY).nav == Decimal("2850")
    assert NAVHistory.objects.get(security=crypto, date=_TODAY).source == "coingecko"


def test_refresh_prices_from_bulk_maps_without_per_security_calls(monkeypatch):
    """MF resolves from the AMFI NAVAll snapshot, equity from the NSE bhavcopy —
    the per-security feeds are never called (2 requests instead of N+M)."""
    monkeypatch.setattr(
        refresh_navs_mod.amfi_bulk,
        "fetch_all_latest",
        lambda **_: {
            "122639": NAVPoint(date=_TODAY, nav=Decimal("75.5")),
            "INE002A01018": NAVPoint(date=_TODAY, nav=Decimal("2850")),
        },
    )
    monkeypatch.setattr(
        refresh_navs_mod.nse_bhavcopy,
        "fetch_close_by_symbol",
        lambda *_a, **_k: {"RELIANCE": NAVPoint(date=_TODAY, nav=Decimal("2850"))},
    )

    def _must_not_call(*_a, **_k):
        raise AssertionError("per-security feed hit despite bulk data")

    monkeypatch.setattr(mfapi, "fetch_latest_nav", _must_not_call)
    monkeypatch.setattr(nse_history, "fetch_history", _must_not_call)
    monkeypatch.setattr(yfinance_feed, "fetch_quote", _must_not_call)

    mf = Security.objects.create(
        security_type=SecurityType.MF.value, name="Fund", amfi_code="122639"
    )
    eq = Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
    )

    summary = refresh_navs()

    assert summary["updated"] == 2 and summary["errors"] == 0
    assert NAVHistory.objects.get(security=mf, date=_TODAY).source == "amfi"
    point = NAVHistory.objects.get(security=eq, date=_TODAY)
    assert point.nav == Decimal("2850") and point.source == "nse-bhavcopy"


def test_refresh_falls_back_per_security_when_absent_from_bulk(mocked_feeds):
    """A scheme missing from the bulk snapshot (new / obscure) still prices via the
    per-scheme feed — the bulk maps are empty here (autouse default)."""
    mf = Security.objects.create(
        security_type=SecurityType.MF.value, name="New Fund", amfi_code="999999"
    )

    summary = refresh_navs()

    assert summary["updated"] == 1
    assert NAVHistory.objects.get(security=mf, date=_TODAY).nav == Decimal("75.5")  # mfapi latest


def test_equity_prices_nse_first_even_when_yahoo_throttles(monkeypatch):
    """Indian equities price from NSE first (the last row of the security-wise history).
    A Yahoo 429 *raises*, so leading with Yahoo would skip the fallback and freeze the
    price (the bug that stalled demo equity NAVs); NSE-first means a throttled Yahoo
    never blocks an NSE-listed scrip."""
    monkeypatch.setattr(
        nse_history,
        "fetch_history",
        lambda *_a, **_k: SimpleNamespace(points=[NAVPoint(date=_TODAY, nav=Decimal("1234"))]),
    )

    def _yahoo_429(*_a, **_k):
        raise PriceFetchError("yahoo 429")

    monkeypatch.setattr(yfinance_feed, "fetch_quote", _yahoo_429)
    eq = Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
    )

    summary = refresh_navs()

    assert summary["updated"] == 1 and summary["errors"] == 0
    point = NAVHistory.objects.get(security=eq, date=_TODAY)
    assert point.nav == Decimal("1234")
    assert point.source == "nse"


def test_equity_falls_back_to_yahoo_when_nse_has_no_data(mocked_feeds):
    """NSE miss (returns None) → Yahoo fallback. The autouse NSE stub returns None, so the
    equity resolves via the mocked Yahoo feed."""
    eq = Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="Infosys",
        isin="INE009A01021",
        symbol="INFY",
    )

    summary = refresh_navs()

    assert summary["updated"] == 1
    assert NAVHistory.objects.get(security=eq, date=_TODAY).source == "yfinance"


def test_foreign_equity_skips_nse_and_uses_yahoo(monkeypatch):
    """Foreign equities have no NSE listing, so NSE is never queried — straight to Yahoo."""
    nse_calls = {"n": 0}

    def _nse_spy(*_a, **_k):
        nse_calls["n"] += 1
        return None

    monkeypatch.setattr(nse_history, "fetch_history", _nse_spy)
    monkeypatch.setattr(
        yfinance_feed,
        "fetch_quote",
        lambda symbol, **_: Quote(
            as_of=_TODAY, price=Decimal("190"), currency="USD", source="yfinance"
        ),
    )
    eq = Security.objects.create(
        security_type=SecurityType.FOREIGN_EQUITY.value,
        name="Apple Inc",
        symbol="AAPL",
        exchange="NASDAQ",
    )

    summary = refresh_navs()

    assert summary["updated"] == 1
    assert nse_calls["n"] == 0  # foreign equity → NSE never queried
    assert NAVHistory.objects.get(security=eq, date=_TODAY).source == "yfinance"


def test_refresh_skips_securities_with_no_feed(mocked_feeds):
    # A bond with no symbol has no price feed -> skipped, not errored.
    Security.objects.create(
        security_type=SecurityType.BOND.value, name="REC Bond", isin="INE020B08DG9"
    )
    summary = refresh_navs()
    assert summary["updated"] == 0
    assert summary["skipped"] == 1


def test_refresh_is_idempotent_on_same_day(mocked_feeds):
    Security.objects.create(security_type=SecurityType.MF.value, name="Fund", amfi_code="122639")
    refresh_navs()
    refresh_navs()  # same date -> updated in place
    assert NAVHistory.objects.filter(date=_TODAY).count() == 1


def test_refresh_records_feed_errors(monkeypatch):
    def _boom(code, **_):
        raise mfapi.NAVFetchError("mfapi down")

    monkeypatch.setattr(mfapi, "fetch_latest_nav", _boom)
    Security.objects.create(security_type=SecurityType.MF.value, name="Fund", amfi_code="122639")
    summary = refresh_navs()
    assert summary["errors"] == 1
    assert NAVHistory.objects.count() == 0


def test_backfill_fills_a_multi_day_gap(monkeypatch):
    """A fund days behind is brought fully current — every missing date is inserted,
    not just the newest point. This is what keeps a fortnight-old desktop gapless."""
    today = dt.date.today()
    old = today - dt.timedelta(days=10)
    points = []
    d = old
    while d <= today:
        points.append(NAVPoint(date=d, nav=Decimal("11")))
        d += dt.timedelta(days=1)
    monkeypatch.setattr(
        mfapi, "fetch_nav_history", lambda code, **_: SimpleNamespace(points=points, isin="")
    )
    mf = Security.objects.create(security_type=SecurityType.MF.value, name="F", amfi_code="100001")
    NAVHistory.objects.create(security=mf, date=old, nav=Decimal("10"))  # only old point on file

    summary = backfill_missing_history(securities=Security.objects.filter(id=mf.id))

    assert summary["skipped"] == 0  # behind by >1 trading day → not skipped
    assert NAVHistory.objects.filter(security=mf).count() == len(points)  # gap filled to today
    assert NAVHistory.objects.filter(security=mf, date=today).exists()


def test_backfill_skips_a_fund_current_to_last_trading_day(monkeypatch):
    """A fund already current (within the trading-day grace) isn't re-fetched."""
    calls = {"n": 0}

    def _spy(code, **_):
        calls["n"] += 1
        return SimpleNamespace(points=[], isin="")

    monkeypatch.setattr(mfapi, "fetch_nav_history", _spy)
    mf = Security.objects.create(security_type=SecurityType.MF.value, name="F", amfi_code="100002")
    NAVHistory.objects.create(
        security=mf, date=last_trading_day(dt.date.today()), nav=Decimal("10")
    )

    summary = backfill_missing_history(securities=Security.objects.filter(id=mf.id))

    assert summary["skipped"] == 1
    assert calls["n"] == 0  # current → no network call


def test_backfill_force_refetches_a_current_fund_to_repair_interior_holes(monkeypatch):
    """--force re-pulls even a fund whose latest point is current, filling earlier
    missing dates the freshness check can't detect."""
    cutoff = last_trading_day(dt.date.today())
    gap_day = cutoff - dt.timedelta(days=7)
    monkeypatch.setattr(
        mfapi,
        "fetch_nav_history",
        lambda code, **_: SimpleNamespace(
            points=[
                NAVPoint(date=gap_day, nav=Decimal("9")),
                NAVPoint(date=cutoff, nav=Decimal("10")),
            ],
            isin="",
        ),
    )
    mf = Security.objects.create(security_type=SecurityType.MF.value, name="F", amfi_code="100003")
    NAVHistory.objects.create(security=mf, date=cutoff, nav=Decimal("10"))  # current → would skip

    # Without force: current, so skipped — the interior hole stays.
    assert backfill_missing_history(securities=Security.objects.filter(id=mf.id))["skipped"] == 1
    assert not NAVHistory.objects.filter(security=mf, date=gap_day).exists()

    # With force: re-pulled, the earlier missing date is filled.
    summary = backfill_missing_history(securities=Security.objects.filter(id=mf.id), force=True)
    assert summary["skipped"] == 0
    assert NAVHistory.objects.filter(security=mf, date=gap_day).exists()


def test_backfill_flags_dead_code_as_closed(monkeypatch):
    """The feed responds with NO history for a fund we hold no NAV for → its code is
    dead (matured/delisted). Flag nav_feed_closed so valuation degrades it."""
    monkeypatch.setattr(
        mfapi, "fetch_nav_history", lambda code, **_: SimpleNamespace(points=[], isin="")
    )
    mf = Security.objects.create(
        security_type=SecurityType.MF.value, name="Matured CEF", amfi_code="999999"
    )

    summary = backfill_missing_history(securities=Security.objects.filter(id=mf.id))

    assert summary["closed"] == 1
    mf.refresh_from_db()
    assert mf.nav_feed_closed is True
    assert NAVHistory.objects.filter(security=mf).count() == 0


def test_backfill_feed_error_does_not_flag_closed(monkeypatch):
    """A transient feed error must NOT be mistaken for a dead code — stays retryable."""

    def _boom(code, **_):
        raise mfapi.NAVFetchError("mfapi 502")

    monkeypatch.setattr(mfapi, "fetch_nav_history", _boom)
    mf = Security.objects.create(
        security_type=SecurityType.MF.value, name="Fund", amfi_code="122639"
    )

    summary = backfill_missing_history(securities=Security.objects.filter(id=mf.id))

    assert summary["errors"] == 1 and summary["closed"] == 0
    mf.refresh_from_db()
    assert mf.nav_feed_closed is False


def test_backfill_reopens_closed_code_when_data_returns(monkeypatch):
    """Self-healing: a previously-dead code that starts returning data is reopened."""
    monkeypatch.setattr(
        mfapi,
        "fetch_nav_history",
        lambda code, **_: SimpleNamespace(
            points=[NAVPoint(date=_TODAY, nav=Decimal("10"))], isin=""
        ),
    )
    mf = Security.objects.create(
        security_type=SecurityType.MF.value, name="Fund", amfi_code="122639", nav_feed_closed=True
    )

    backfill_missing_history(securities=Security.objects.filter(id=mf.id))

    mf.refresh_from_db()
    assert mf.nav_feed_closed is False
    assert NAVHistory.objects.filter(security=mf).exists()


def test_backfill_prefers_captnemo_when_isin_known(monkeypatch):
    """A fund with an ISIN backfills from captnemo (the faster, full-series feed);
    mfapi isn't touched."""
    mfapi_calls = {"n": 0}

    def _mfapi_spy(code, **_):
        mfapi_calls["n"] += 1
        return SimpleNamespace(points=[], isin="")

    monkeypatch.setattr(mfapi, "fetch_nav_history", _mfapi_spy)
    monkeypatch.setattr(
        captnemo,
        "fetch_nav_history",
        lambda isin, **_: SimpleNamespace(
            points=[NAVPoint(date=_TODAY, nav=Decimal("42"))], isin=isin
        ),
    )
    mf = Security.objects.create(
        security_type=SecurityType.MF.value, name="F", amfi_code="100010", isin="INF000X00010"
    )

    backfill_missing_history(securities=Security.objects.filter(id=mf.id))

    assert mfapi_calls["n"] == 0  # captnemo served it; mfapi never called
    point = NAVHistory.objects.get(security=mf, date=_TODAY)
    assert point.nav == Decimal("42")
    assert point.source == "captnemo"


def test_backfill_falls_back_to_mfapi_when_captnemo_fails(monkeypatch):
    """captnemo down → mfapi (by AMFI code) backstops, and the points are tagged mfapi."""

    def _captnemo_boom(isin, **_):
        raise captnemo.NAVFetchError("captnemo 503")

    monkeypatch.setattr(captnemo, "fetch_nav_history", _captnemo_boom)
    monkeypatch.setattr(
        mfapi,
        "fetch_nav_history",
        lambda code, **_: SimpleNamespace(
            points=[NAVPoint(date=_TODAY, nav=Decimal("43"))], isin="INF000X00011"
        ),
    )
    mf = Security.objects.create(
        security_type=SecurityType.MF.value, name="F", amfi_code="100011", isin="INF000X00011"
    )

    backfill_missing_history(securities=Security.objects.filter(id=mf.id))

    point = NAVHistory.objects.get(security=mf, date=_TODAY)
    assert point.nav == Decimal("43")
    assert point.source == "mfapi"


def test_backfill_learns_isin_from_mfapi_meta(monkeypatch):
    """A fund known only by AMFI code backfills via mfapi; its meta ISIN is persisted
    so captnemo can lead next time."""
    monkeypatch.setattr(
        mfapi,
        "fetch_nav_history",
        lambda code, **_: SimpleNamespace(
            points=[NAVPoint(date=_TODAY, nav=Decimal("44"))], isin="INF000X00012"
        ),
    )
    mf = Security.objects.create(
        security_type=SecurityType.MF.value, name="F", amfi_code="100012"
    )  # no isin yet

    backfill_missing_history(securities=Security.objects.filter(id=mf.id))

    mf.refresh_from_db()
    assert mf.isin == "INF000X00012"  # learned from mfapi meta


def test_daily_refresh_falls_back_to_captnemo_when_mfapi_errors(monkeypatch):
    """mfapi /latest blip on a fund we also know by ISIN → captnemo covers the day."""

    def _mfapi_boom(code, **_):
        raise mfapi.NAVFetchError("mfapi 502")

    monkeypatch.setattr(mfapi, "fetch_latest_nav", _mfapi_boom)
    monkeypatch.setattr(
        captnemo,
        "fetch_latest_nav",
        lambda isin, **_: NAVPoint(date=_TODAY, nav=Decimal("45")),
    )
    mf = Security.objects.create(
        security_type=SecurityType.MF.value, name="F", amfi_code="100013", isin="INF000X00013"
    )

    summary = refresh_navs(securities=Security.objects.filter(id=mf.id))

    assert summary["updated"] == 1 and summary["errors"] == 0
    point = NAVHistory.objects.get(security=mf, date=_TODAY)
    assert point.nav == Decimal("45")
    assert point.source == "captnemo"


def test_refresh_navs_command(mocked_feeds):
    Security.objects.create(security_type=SecurityType.MF.value, name="Fund", amfi_code="122639")
    call_command("refresh_navs")
    assert NAVHistory.objects.filter(date=_TODAY).count() == 1


def test_reconcile_all_command(make_investor):
    inv = make_investor()
    create_manual_transaction(
        inv,
        {
            "security_type": "equity",
            "name": "Reliance",
            "isin": "INE002A01018",
            "folio_number": "1208160000000001",
            "broker": "ZERODHA",
            "date": dt.date(2024, 1, 1),
            "transaction_type": "buy",
            "units": Decimal("10"),
            "price": Decimal("100"),
        },
    )
    # Wipe the import-time status, then prove reconcile_all rebuilds it.
    inv.integrity_statuses.all().delete()
    call_command("reconcile_all")
    assert inv.integrity_statuses.count() == 1
