"""Day-wise valuation jobs: recompute correctness, retry/backoff, provisional
supersede, and the scheduler's pending-claim logic. Feeds are stubbed (no live
HTTP) — NAVHistory is created directly, as in test_refresh_navs."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone
from folioman_app.models import InvestorValue, NAVHistory, ValuationStatus
from folioman_app.tasks import valuation_jobs
from folioman_core.models import SecurityType

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _stub_feeds(monkeypatch):
    """No network: the job's NAV-ensure step is a no-op; tests seed NAVHistory."""
    monkeypatch.setattr(valuation_jobs, "refresh_navs", lambda **kw: {"updated": 0})
    monkeypatch.setattr(valuation_jobs, "backfill_missing_history", lambda **kw: {"points": 0})


def _values(investor) -> dict[dt.date, Decimal]:
    return {
        v.date: v.value_inr
        for v in InvestorValue.objects.filter(investor=investor).order_by("date")
    }


def test_recompute_writes_daily_series_with_carry_forward(
    make_investor, make_security, make_folio, make_transaction
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    folio = make_folio(investor=inv)
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2025, 1, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 1, 1), nav=Decimal("10"))
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 3, 1), nav=Decimal("15"))

    status = valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))

    assert status == ValuationStatus.READY
    inv.refresh_from_db()
    assert inv.valuation_status == ValuationStatus.READY
    assert inv.valuation_computed_through == timezone.localdate()
    vals = _values(inv)
    assert vals[dt.date(2025, 2, 1)] == Decimal("1000")  # 100 units x NAV-on-or-before (10)
    assert vals[dt.date(2025, 3, 1)] == Decimal("1500")  # 100 units x 15
    assert vals[dt.date(2025, 3, 15)] == Decimal("1500")  # NAV carried forward


def test_recompute_supersedes_the_provisional_point(
    make_investor, make_security, make_folio, make_transaction
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)
    folio = make_folio(investor=inv)
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2025, 1, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 1, 1), nav=Decimal("10"))
    # A provisional point seeded at import (statement value), to be replaced.
    InvestorValue.objects.create(
        investor=inv,
        date=dt.date(2025, 1, 1),
        value_inr=Decimal("9999"),
        invested_inr=Decimal("9999"),
        is_provisional=True,
    )

    valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))

    assert not InvestorValue.objects.filter(investor=inv, is_provisional=True).exists()
    assert _values(inv)[dt.date(2025, 1, 1)] == Decimal("1000")  # real value, not 9999


def test_unpriced_mf_errors_then_recovers_on_retry(
    make_investor, make_security, make_folio, make_transaction
):
    inv = make_investor()
    mf = make_security(security_type=SecurityType.MF.value)  # no NAVHistory yet
    folio = make_folio(investor=inv)
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2025, 1, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )

    status = valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))
    assert status == ValuationStatus.ERROR
    inv.refresh_from_db()
    assert inv.valuation_attempts == 1
    assert inv.valuation_next_attempt_at is not None  # scheduled for retry
    assert not InvestorValue.objects.filter(investor=inv).exists()

    # Feed recovers: NAV arrives, the backoff has elapsed → the tick recomputes.
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 1, 1), nav=Decimal("10"))
    inv.valuation_next_attempt_at = timezone.now() - dt.timedelta(minutes=1)
    inv.save(update_fields=["valuation_next_attempt_at"])

    processed = valuation_jobs.process_pending_valuations()
    assert processed == 1
    inv.refresh_from_db()
    assert inv.valuation_status == ValuationStatus.READY
    assert InvestorValue.objects.filter(investor=inv).exists()


def test_process_pending_skips_error_not_yet_due(make_investor):
    inv = make_investor()
    inv.valuation_status = ValuationStatus.ERROR
    inv.valuation_next_attempt_at = timezone.now() + dt.timedelta(minutes=30)  # future
    inv.save()
    assert valuation_jobs.process_pending_valuations() == 0


def test_empty_investor_is_ready_with_no_rows(make_investor):
    inv = make_investor()
    status = valuation_jobs.recompute_investor_valuation(inv.id)
    assert status == ValuationStatus.READY
    assert not InvestorValue.objects.filter(investor=inv).exists()


def test_queue_recompute_seeds_provisional_and_marks_computing(make_investor):
    inv = make_investor()
    valuation_jobs.queue_recompute(
        inv,
        dt.date(2024, 4, 1),
        provisional_value=Decimal("50000"),
        provisional_invested=Decimal("40000"),
        as_of=dt.date(2025, 3, 31),
    )
    inv.refresh_from_db()
    assert inv.valuation_status == ValuationStatus.COMPUTING
    assert inv.valuation_recompute_from == dt.date(2024, 4, 1)
    prov = InvestorValue.objects.get(investor=inv, date=dt.date(2025, 3, 31))
    assert prov.is_provisional and prov.value_inr == Decimal("50000")
