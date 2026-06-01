"""Investor summary API: INR value + tax-ready split + last-import date."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import NAVHistory, SecurityIntegrityStatus
from folioman_app.models.jobs import ImportJob, ImportJobStatus, ImportKind
from folioman_core.models import SecurityType
from folioman_core.reconciliation import IntegrityStatus

pytestmark = pytest.mark.django_db


def test_summary_values_holdings_in_inr(client, make_investor, make_security, make_holding):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    make_holding(investor=inv, security=mf, units=Decimal("100"), as_of_date=dt.date(2025, 6, 1))
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("75"))

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()

    assert Decimal(str(body["total_inr"])) == Decimal("7500")
    assert body["holdings_count"] == 1
    assert body["stale_count"] == 0
    assert body["last_import_at"] is None


def test_summary_values_full_history_ledger_without_snapshot(
    client, make_investor, make_security, make_transaction
):
    # A full-history MF scheme has a transaction ledger but NO holding snapshot.
    # Net worth must value it from the ledger's current units (else it shows ₹0).
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    make_transaction(investor=inv, security=mf, units=Decimal("100"), nav_or_price=Decimal("10"))
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("75"))

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()

    assert Decimal(str(body["total_inr"])) == Decimal("7500")  # 100 ledger units * 75
    assert body["holdings_count"] == 1


def test_summary_counts_tax_ready_and_attention(client, make_investor, make_security, make_folio):
    inv = make_investor()
    folio = make_folio(investor=inv)
    ready = make_security()
    snap = make_security()
    broken = make_security()
    SecurityIntegrityStatus.objects.create(
        investor=inv,
        security=ready,
        folio=folio,
        status=IntegrityStatus.FULL_HISTORY.value,
        tax_safe=True,
    )
    SecurityIntegrityStatus.objects.create(
        investor=inv,
        security=snap,
        folio=folio,
        status=IntegrityStatus.SNAPSHOT_ONLY.value,
        tax_safe=False,
    )
    SecurityIntegrityStatus.objects.create(
        investor=inv,
        security=broken,
        folio=folio,
        status=IntegrityStatus.MISMATCH.value,
        tax_safe=False,
    )

    body = client.get(f"/api/investors/{inv.id}/summary").json()

    assert body["tax_ready_count"] == 1
    assert body["snapshot_count"] == 1
    assert body["needs_attention_count"] == 1


def test_summary_reports_last_successful_import(client, make_investor):
    inv = make_investor()
    ImportJob.objects.create(investor=inv, kind=ImportKind.CSV.value, status=ImportJobStatus.FAILED)
    ImportJob.objects.create(
        investor=inv, kind=ImportKind.CAS.value, status=ImportJobStatus.SUCCESS
    )

    body = client.get(f"/api/investors/{inv.id}/summary").json()

    assert body["last_import_at"] is not None


def test_summary_unknown_investor_404(client):
    assert client.get("/api/investors/999999/summary").status_code == 404
