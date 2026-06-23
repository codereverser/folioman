"""Equity dividend attribution and scheme-detail timeline."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import CorporateActionReference
from folioman_app.services.dividends import (
    attribute_dividends_for_folio,
    build_equity_dividend_detail,
)
from folioman_app.tasks.reconcile import reconcile_security
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.models import SecurityType

pytestmark = pytest.mark.django_db

_ISIN = "INE002A01018"
_DEMAT = "1208160001234567"
_HEADER = "security_type,name,symbol,isin,date,transaction_type,units,price,folio_number,broker\n"


def _import_equity(inv, *, units="100", on="2024-01-15"):
    from folioman_app.models import ImportJob
    from folioman_app.models.jobs import ImportKind
    from folioman_app.tasks.import_csv import process_csv

    row = f"equity,Reliance,RELIANCE,{_ISIN},{on},buy,{units},2500,{_DEMAT},ZERODHA\n"
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_HEADER + row).encode(), "")


def test_attribute_dividends_creates_ledger_rows(make_investor):
    inv = make_investor()
    _import_equity(inv)
    from folioman_app.models import Folio, Security

    sec = Security.objects.get(isin=_ISIN)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    CorporateActionReference.objects.create(
        security=sec,
        isin=_ISIN,
        symbol="RELIANCE",
        exchange="NSE",
        ex_date=dt.date(2024, 8, 1),
        subject="Interim Dividend - Rs 10 Per Share",
        parsed_type=CorpActionType.DIVIDEND.value,
        amount=Decimal("10"),
        needs_review=False,
        source="NSE",
    )
    created = attribute_dividends_for_folio(inv, folio, sec)
    assert created == 1
    div = inv.transactions.get(security=sec, transaction_type="dividend")
    assert div.amount == Decimal("1000")
    assert div.source_ref == "dividend:ca-ref:1"


def test_attribute_dividends_idempotent(make_investor):
    inv = make_investor()
    _import_equity(inv)
    from folioman_app.models import Folio, Security

    sec = Security.objects.get(isin=_ISIN)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    CorporateActionReference.objects.create(
        security=sec,
        isin=_ISIN,
        symbol="RELIANCE",
        exchange="NSE",
        ex_date=dt.date(2024, 8, 1),
        subject="Interim Dividend - Rs 10 Per Share",
        parsed_type=CorpActionType.DIVIDEND.value,
        amount=Decimal("10"),
        needs_review=False,
        source="NSE",
    )
    attribute_dividends_for_folio(inv, folio, sec)
    again = attribute_dividends_for_folio(inv, folio, sec)
    assert again == 0
    assert inv.transactions.filter(security=sec, transaction_type="dividend").count() == 1


def test_reconcile_triggers_dividend_attribution(make_investor):
    inv = make_investor()
    _import_equity(inv)
    from folioman_app.models import Security

    sec = Security.objects.get(isin=_ISIN)
    CorporateActionReference.objects.create(
        security=sec,
        isin=_ISIN,
        symbol="RELIANCE",
        exchange="NSE",
        ex_date=dt.date(2024, 8, 1),
        subject="Interim Dividend - Rs 10 Per Share",
        parsed_type=CorpActionType.DIVIDEND.value,
        amount=Decimal("10"),
        needs_review=False,
        source="NSE",
    )
    reconcile_security(inv, sec)
    assert inv.transactions.filter(security=sec, transaction_type="dividend").exists()


def test_build_equity_dividend_detail_snapshot_estimate(make_investor, make_security, make_holding):
    inv = make_investor()
    sec = make_security(security_type=SecurityType.EQUITY.value, isin=_ISIN, symbol="RELIANCE")
    make_holding(investor=inv, security=sec, units=Decimal("50"), as_of_date=dt.date(2025, 6, 1))
    CorporateActionReference.objects.create(
        security=sec,
        isin=_ISIN,
        symbol="RELIANCE",
        exchange="NSE",
        ex_date=dt.date(2025, 9, 1),
        subject="Interim Dividend - Rs 2 Per Share",
        parsed_type=CorpActionType.DIVIDEND.value,
        amount=Decimal("2"),
        needs_review=False,
        source="NSE",
    )
    detail = build_equity_dividend_detail(
        inv,
        sec,
        as_of=dt.date(2025, 6, 1),
        current_units=Decimal("50"),
        invested_inr=None,
    )
    assert len(detail["dividends"]) == 1
    assert detail["dividends"][0]["kind"] == "estimate"
    assert detail["dividends"][0]["amount_inr"] == Decimal("100")
    assert detail["dividends_received_inr"] is None


def test_scheme_detail_includes_dividend_timeline(client, make_investor):
    inv = make_investor()
    _import_equity(inv)
    from folioman_app.models import Security

    sec = Security.objects.get(isin=_ISIN)
    CorporateActionReference.objects.create(
        security=sec,
        isin=_ISIN,
        symbol="RELIANCE",
        exchange="NSE",
        ex_date=dt.date(2024, 8, 1),
        subject="Interim Dividend - Rs 10 Per Share",
        parsed_type=CorpActionType.DIVIDEND.value,
        amount=Decimal("10"),
        needs_review=False,
        source="NSE",
    )
    reconcile_security(inv, sec)

    body = client.get(f"/api/investors/{inv.id}/holdings/{sec.id}", {"as_of": "2025-06-01"}).json()
    assert len(body["dividends"]) == 1
    assert body["dividends"][0]["kind"] == "attributed"
    assert Decimal(str(body["dividends_received_inr"])) == Decimal("1000")
