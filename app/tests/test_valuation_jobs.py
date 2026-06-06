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


def test_recompute_failure_midway_keeps_prior_series(
    monkeypatch, make_investor, make_security, make_folio, make_transaction
):
    """Compute-then-swap resilience: a failure during the swap must never blank the
    investor — the prior (and provisional) values survive, status goes ERROR."""
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
    # A prior committed series + a provisional point that a successful recompute would
    # replace. Both must survive the failed attempt.
    InvestorValue.objects.create(
        investor=inv, date=dt.date(2025, 1, 1), value_inr=Decimal("7777"), invested_inr=Decimal("0")
    )
    InvestorValue.objects.create(
        investor=inv,
        date=dt.date(2025, 1, 2),
        value_inr=Decimal("8888"),
        invested_inr=Decimal("0"),
        is_provisional=True,
    )

    def boom(*a, **k):
        raise RuntimeError("insert blew up mid-swap")

    monkeypatch.setattr(InvestorValue.objects, "bulk_create", boom)

    status = valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))

    assert status == ValuationStatus.ERROR
    inv.refresh_from_db()
    assert inv.valuation_status == ValuationStatus.ERROR
    # The destructive delete is part of the swap — rolled back, so prior values remain.
    vals = _values(inv)
    assert vals[dt.date(2025, 1, 1)] == Decimal("7777")
    assert vals[dt.date(2025, 1, 2)] == Decimal("8888")
    assert InvestorValue.objects.filter(investor=inv, is_provisional=True).exists()


def test_swap_series_never_replaces_with_nothing(make_investor):
    """The swap guards 'never blank': an empty computed series skips the delete, so a
    prior series is left untouched rather than wiped."""
    inv = make_investor()
    InvestorValue.objects.create(
        investor=inv, date=dt.date(2025, 1, 1), value_inr=Decimal("5000"), invested_inr=Decimal("0")
    )

    valuation_jobs._swap_series(inv, dt.date(2025, 1, 1), [])

    assert _values(inv)[dt.date(2025, 1, 1)] == Decimal("5000")


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


def test_unpriceable_mf_does_not_block_the_whole_investor(
    make_investor, make_security, make_folio, make_transaction
):
    """A structurally unpriceable MF (no amfi_code — e.g. an eCAS demat fund the
    ISIN DB can't map, or a matured scheme) must NOT error the whole investor.
    Value the priceable holdings; skip the unpriceable one (it surfaces as
    stale_count), and stay READY — never blank an otherwise-valued portfolio."""
    inv = make_investor()
    priced = make_security(security_type=SecurityType.MF.value)  # auto amfi_code
    unpriceable = make_security(
        security_type=SecurityType.MF.value, amfi_code="", isin="INF0000UNP12"
    )
    folio = make_folio(investor=inv)
    make_transaction(
        investor=inv,
        security=priced,
        folio=folio,
        date=dt.date(2025, 1, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    make_transaction(
        investor=inv,
        security=unpriceable,
        folio=folio,
        date=dt.date(2025, 1, 1),
        units=Decimal("50"),
        nav_or_price=Decimal("20"),
    )
    NAVHistory.objects.create(security=priced, date=dt.date(2025, 1, 1), nav=Decimal("10"))
    # unpriceable has NO NAVHistory and no amfi_code → can never be priced via the feed.

    status = valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))

    assert status == ValuationStatus.READY  # degraded, not errored
    # Series reflects only the priceable holding (100 * 10); the unpriceable is skipped.
    assert _values(inv)[dt.date(2025, 1, 1)] == Decimal("1000")


def test_process_pending_skips_error_not_yet_due(make_investor):
    inv = make_investor()
    inv.valuation_status = ValuationStatus.ERROR
    inv.valuation_next_attempt_at = timezone.now() + dt.timedelta(minutes=30)  # future
    inv.save()
    assert valuation_jobs.process_pending_valuations() == 0


def test_process_pending_primes_navs_once_globally(
    monkeypatch, make_investor, make_security, make_folio, make_transaction
):
    """The tick primes the NAV cache once for all due investors, not per-investor
    (so shared schemes aren't re-fetched — the cause of feed rate-limiting)."""
    calls = {"refresh": 0, "backfill": 0}
    monkeypatch.setattr(
        valuation_jobs,
        "refresh_navs",
        lambda **kw: (calls.__setitem__("refresh", calls["refresh"] + 1), {"updated": 0})[1],
    )
    monkeypatch.setattr(
        valuation_jobs,
        "backfill_missing_history",
        lambda **kw: (calls.__setitem__("backfill", calls["backfill"] + 1), {"points": 0})[1],
    )
    mf = make_security(security_type=SecurityType.MF.value)
    NAVHistory.objects.create(security=mf, date=dt.date(2025, 1, 1), nav=Decimal("10"))
    for _ in range(2):
        inv = make_investor()
        folio = make_folio(investor=inv)
        make_transaction(
            investor=inv,
            security=mf,
            folio=folio,
            date=dt.date(2025, 1, 1),
            units=Decimal("100"),
            nav_or_price=Decimal("10"),
        )
        inv.valuation_status = ValuationStatus.PENDING
        inv.valuation_recompute_from = dt.date(2025, 1, 1)
        inv.save()

    processed = valuation_jobs.process_pending_valuations()

    assert processed == 2
    assert calls["refresh"] == 1  # primed once for the union, not per-investor
    assert calls["backfill"] == 1


def test_empty_investor_is_ready_with_no_rows(make_investor):
    inv = make_investor()
    status = valuation_jobs.recompute_investor_valuation(inv.id)
    assert status == ValuationStatus.READY
    assert not InvestorValue.objects.filter(investor=inv).exists()


def test_catch_up_flags_ready_but_behind_investor(make_investor):
    """Launch catch-up: a READY series behind today is flagged PENDING (so the next
    interval tick brings it current), re-doing its last-computed day."""
    yesterday = timezone.localdate() - dt.timedelta(days=1)
    inv = make_investor()
    inv.valuation_status = ValuationStatus.READY
    inv.valuation_computed_through = yesterday
    inv.save()

    queued = valuation_jobs.enqueue_catch_up_if_stale()

    assert queued == 1
    inv.refresh_from_db()
    assert inv.valuation_status == ValuationStatus.PENDING
    assert inv.valuation_recompute_from == yesterday  # re-do last day, extend to today


def test_catch_up_is_noop_when_all_current(make_investor):
    """Idempotent and quiet: when every READY series is already at today, catch-up
    does nothing — no duplicate work, status untouched."""
    today = timezone.localdate()
    inv = make_investor()
    inv.valuation_status = ValuationStatus.READY
    inv.valuation_computed_through = today
    inv.save()

    assert valuation_jobs.enqueue_catch_up_if_stale() == 0
    inv.refresh_from_db()
    assert inv.valuation_status == ValuationStatus.READY


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
