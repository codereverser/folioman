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
