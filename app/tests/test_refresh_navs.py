"""NAV refresh + management commands. Feeds are mocked — no live HTTP."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.core.management import call_command
from folioman_app.models import NAVHistory, Security
from folioman_app.tasks.import_csv import create_manual_transaction
from folioman_app.tasks.refresh_navs import refresh_navs
from folioman_core.models import NAVPoint, Quote, SecurityType
from folioman_core.price_feeds import coingecko, mfapi, yfinance_feed

pytestmark = pytest.mark.django_db

_TODAY = dt.date(2025, 6, 2)


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
