"""Investor summary API: INR value + tax-ready split + last-import date."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import InvestorValue, NAVHistory, SecurityIntegrityStatus
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


def test_summary_tax_ready_is_counted_per_security_folio(
    client, make_investor, make_security, make_folio
):
    # The SAME fund held in two folios reconciles separately (per-(security, folio)
    # FIFO), so tax-readiness is per folio: one folio full-history (tax-ready), the
    # other snapshot-only (not). The fraction must read "1 of 2", not "1 of 1" — the
    # integrity-unit count is the denominator, not the per-security holdings count.
    inv = make_investor()
    fund = make_security(security_type=SecurityType.MF.value)
    folio_a = make_folio(investor=inv)
    folio_b = make_folio(investor=inv)
    SecurityIntegrityStatus.objects.create(
        investor=inv,
        security=fund,
        folio=folio_a,
        status=IntegrityStatus.FULL_HISTORY.value,
        tax_safe=True,
    )
    SecurityIntegrityStatus.objects.create(
        investor=inv,
        security=fund,
        folio=folio_b,
        status=IntegrityStatus.SNAPSHOT_ONLY.value,
        tax_safe=False,
    )

    body = client.get(f"/api/investors/{inv.id}/summary").json()

    assert body["integrity_unit_count"] == 2  # two (security, folio) units
    assert body["tax_ready_count"] == 1  # only the full-history folio


def test_summary_counts_unpriced_funds_excluding_snapshots(
    client, make_investor, make_security, make_holding
):
    # A held MF with no NAV is a fixable pricing gap → counted (the total excludes
    # it). A held equity with no price is a v1 snapshot (no symbol feed yet) →
    # NOT counted; that's unpriced by design, not a gap to flag.
    inv = make_investor()
    fund = make_security(security_type=SecurityType.MF.value)  # no NAVHistory
    equity = make_security(
        security_type=SecurityType.EQUITY.value, isin="INE000000001", symbol="ABC"
    )
    make_holding(investor=inv, security=fund, units=Decimal("10"), as_of_date=dt.date(2025, 6, 1))
    make_holding(investor=inv, security=equity, units=Decimal("5"), as_of_date=dt.date(2025, 6, 1))

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()

    assert body["unpriced_fund_count"] == 1  # the fund only; the equity is excluded


def test_summary_reports_last_successful_import(client, make_investor):
    inv = make_investor()
    ImportJob.objects.create(investor=inv, kind=ImportKind.CSV.value, status=ImportJobStatus.FAILED)
    ImportJob.objects.create(
        investor=inv, kind=ImportKind.CAS.value, status=ImportJobStatus.SUCCESS
    )

    body = client.get(f"/api/investors/{inv.id}/summary").json()

    assert body["last_import_at"] is not None


def test_summary_falls_back_to_last_known_value_when_unpriced(
    client, make_investor, make_security, make_holding
):
    # A holding exists but there's no NAV yet (fresh import, NAVs not fetched), so
    # the live valuation is ₹0. The headline must fall back to the most recent
    # persisted InvestorValue — reported as of *its* date and flagged provisional —
    # instead of a misleading "₹0 as of today".
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    make_holding(investor=inv, security=mf, units=Decimal("100"), as_of_date=dt.date(2025, 5, 31))
    # No NAVHistory → nothing priced.
    InvestorValue.objects.create(
        investor=inv,
        date=dt.date(2025, 5, 31),
        value_inr=Decimal("32082650.98"),
        invested_inr=Decimal("21565739.16"),
        is_provisional=True,
    )

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-04"}).json()

    assert Decimal(str(body["total_inr"])) == Decimal("32082650.98")
    assert body["is_provisional"] is True
    assert body["as_of"] == "2025-05-31"  # the value's own date, not the query date


def test_summary_priced_value_is_not_flagged_provisional(
    client, make_investor, make_security, make_holding
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    make_holding(investor=inv, security=mf, units=Decimal("100"), as_of_date=dt.date(2025, 6, 1))
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 6, 1), nav=Decimal("75"))

    body = client.get(f"/api/investors/{inv.id}/summary", {"as_of": "2025-06-01"}).json()

    assert Decimal(str(body["total_inr"])) == Decimal("7500")
    assert body["is_provisional"] is False
    assert body["as_of"] == "2025-06-01"


def test_summary_empty_portfolio_stays_zero_not_provisional(client, make_investor):
    # No holdings → genuinely ₹0; must not fabricate a provisional value.
    inv = make_investor()
    body = client.get(f"/api/investors/{inv.id}/summary").json()
    assert Decimal(str(body["total_inr"])) == Decimal("0")
    assert body["is_provisional"] is False


def test_summary_unknown_investor_404(client):
    assert client.get("/api/investors/999999/summary").status_code == 404
