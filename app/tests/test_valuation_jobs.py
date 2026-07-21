"""Day-wise valuation jobs: recompute correctness, retry/backoff, provisional
supersede, and the scheduler's pending-claim logic. Feeds are stubbed (no live
HTTP) — NAVHistory is created directly, as in test_refresh_navs."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone
from folioman_app.models import InvestorValue, NAVHistory, ValuationStatus
from folioman_app.services.valuation import (
    build_investor_summary,
    compute_portfolio_period_returns,
)
from folioman_app.tasks import valuation_jobs
from folioman_core.models import SecurityType, TransactionType
from folioman_core.models.investor import FolioType

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _stub_feeds(monkeypatch):
    """No network: the job's NAV-ensure step is a no-op; tests seed NAVHistory."""
    monkeypatch.setattr(valuation_jobs, "refresh_navs", lambda **kw: {"updated": 0})
    monkeypatch.setattr(valuation_jobs, "extend_tails", lambda **kw: {"securities": 0})


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
    """Compute-then-upsert resilience: a failure during the upsert must never blank the
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
        raise RuntimeError("upsert blew up midway")

    monkeypatch.setattr(InvestorValue.objects, "bulk_create", boom)

    status = valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))

    assert status == ValuationStatus.ERROR
    inv.refresh_from_db()
    assert inv.valuation_status == ValuationStatus.ERROR
    # The upsert has no destructive delete, so a failed attempt leaves prior rows as-is.
    vals = _values(inv)
    assert vals[dt.date(2025, 1, 1)] == Decimal("7777")
    assert vals[dt.date(2025, 1, 2)] == Decimal("8888")
    assert InvestorValue.objects.filter(investor=inv, is_provisional=True).exists()


def test_upsert_series_empty_is_a_noop(make_investor):
    """An empty computed series leaves a prior series untouched (never blanks)."""
    inv = make_investor()
    InvestorValue.objects.create(
        investor=inv, date=dt.date(2025, 1, 1), value_inr=Decimal("5000"), invested_inr=Decimal("0")
    )

    valuation_jobs._upsert_series(inv, [])

    assert _values(inv)[dt.date(2025, 1, 1)] == Decimal("5000")


def test_recompute_leaves_no_orphan_rows_and_stays_daily_contiguous(
    make_investor, make_security, make_folio, make_transaction
):
    """Upsert contract: the stored series exactly matches the recomputed daily set —
    one row per calendar day, no gaps, no duplicates, no stale rows. A second recompute
    with a corrected NAV overwrites in place rather than appending."""
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
    nav = NAVHistory.objects.create(security=mf, date=dt.date(2025, 1, 1), nav=Decimal("10"))

    valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))
    first = _values(inv)
    today = timezone.localdate()
    expected_days = (today - dt.date(2025, 1, 1)).days + 1
    assert len(first) == expected_days  # one row per calendar day, contiguous
    assert min(first) == dt.date(2025, 1, 1) and max(first) == today
    assert all(v == Decimal("1000") for v in first.values())  # 100 units x NAV 10

    # Correct the NAV and recompute from the same start: rows overwrite in place,
    # leaving no orphan/duplicate rows from the prior series.
    nav.nav = Decimal("12")
    nav.save()
    valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))
    second = _values(inv)
    assert len(second) == expected_days  # no extra rows appended
    assert set(second) == set(first)  # identical date set — nothing orphaned
    assert all(v == Decimal("1200") for v in second.values())  # corrected NAV applied


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


def test_closed_fund_degrades_investor_to_ready_not_error(
    make_investor, make_security, make_folio, make_transaction
):
    """A matured/delisted fund (HAS an amfi_code but the feed confirmed no NAV, so
    nav_feed_closed) must degrade like an unmapped one — not error and retry forever.
    The priceable holding values; the closed fund falls out; investor stays READY."""
    inv = make_investor()
    priced = make_security(security_type=SecurityType.MF.value)  # auto amfi_code + NAV below
    closed = make_security(security_type=SecurityType.MF.value, nav_feed_closed=True)  # has code
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
        security=closed,
        folio=folio,
        date=dt.date(2025, 1, 1),
        units=Decimal("50"),
        nav_or_price=Decimal("20"),
    )
    NAVHistory.objects.create(security=priced, date=dt.date(2025, 1, 1), nav=Decimal("10"))
    # `closed` has an amfi_code but NO NAVHistory — without the flag it would be treated
    # as feed-pending and error the investor; the flag makes it degrade instead.

    status = valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))

    assert status == ValuationStatus.READY  # degraded, not errored
    assert _values(inv)[dt.date(2025, 1, 1)] == Decimal("1000")  # only the priceable holding


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
        "extend_tails",
        lambda **kw: (calls.__setitem__("backfill", calls["backfill"] + 1), {"securities": 0})[1],
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


def test_daily_extend_redoes_a_current_ready_investor(make_investor):
    """The 6-hourly revalue re-queues even a series already at today (re-doing today)
    so a NAV that posted after the last run is picked up the same day."""
    today = timezone.localdate()
    inv = make_investor()
    inv.valuation_status = ValuationStatus.READY
    inv.valuation_computed_through = today
    inv.save()

    queued = valuation_jobs.enqueue_daily_extend()

    assert queued == 1
    inv.refresh_from_db()
    assert inv.valuation_status == ValuationStatus.PENDING
    assert inv.valuation_recompute_from == today  # re-do today, not a no-op


def test_catch_up_fires_when_navs_stale_even_if_valuation_current(
    make_investor, make_security, make_holding
):
    """Force a refresh on open when the feed is stale: a series valued through today
    but whose freshest NAV is days old still gets kicked, so opening the app fetches
    the latest prices in the background."""
    today = timezone.localdate()
    old = today - dt.timedelta(days=10)  # comfortably >1 trading day behind today
    inv = make_investor()
    inv.valuation_status = ValuationStatus.READY
    inv.valuation_computed_through = today
    inv.save()
    mf = make_security(security_type=SecurityType.MF.value)
    make_holding(investor=inv, security=mf, units=Decimal("100"), as_of_date=old)
    NAVHistory.objects.create(security=mf, date=old, nav=Decimal("50"))

    assert valuation_jobs.enqueue_catch_up_if_stale() > 0
    inv.refresh_from_db()
    assert inv.valuation_status == ValuationStatus.PENDING


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


# --- equities in net worth ----------------------------------------------------


def test_recompute_series_ledger_only_excludes_snapshot_equity(
    make_investor, make_security, make_folio, make_transaction, make_holding
):
    """Two-tier valuation: snapshot-only equities price into headline net worth but
    not the day-wise trend — only the MF ledger enters the persisted series."""
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

    equity = make_security(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
        exchange="NSE",
    )
    make_holding(investor=inv, security=equity, as_of_date=dt.date(2025, 1, 1), units=Decimal("10"))
    NAVHistory.objects.create(security=equity, date=dt.date(2025, 1, 1), nav=Decimal("1400"))

    status = valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))
    assert status == ValuationStatus.READY
    assert _values(inv)[dt.date(2025, 1, 1)] == Decimal("1000")  # MF ledger only
    summary = build_investor_summary(inv, dt.date(2025, 1, 1))
    assert summary["total_inr"] == Decimal("15000")  # MF + snapshot equity headline


def test_recompute_series_includes_ledger_backed_equity(
    make_investor, make_security, make_folio, make_transaction, make_holding
):
    """A full-history equity tradebook ledger enters the day-wise trend."""
    inv = make_investor()
    demat = make_folio(investor=inv, folio_type=FolioType.DEMAT.value, number="1234567890123456")
    equity = make_security(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
        exchange="NSE",
    )
    make_transaction(
        investor=inv,
        security=equity,
        folio=demat,
        date=dt.date(2025, 1, 1),
        units=Decimal("10"),
        nav_or_price=Decimal("1000"),
    )
    make_transaction(
        investor=inv,
        security=equity,
        folio=demat,
        date=dt.date(2025, 2, 1),
        transaction_type=TransactionType.SELL.value,
        units=Decimal("5"),
        nav_or_price=Decimal("1100"),
    )
    NAVHistory.objects.create(security=equity, date=dt.date(2025, 1, 1), nav=Decimal("1000"))
    NAVHistory.objects.create(security=equity, date=dt.date(2025, 2, 1), nav=Decimal("1100"))

    status = valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))
    assert status == ValuationStatus.READY
    vals = _values(inv)
    assert vals[dt.date(2025, 1, 1)] == Decimal("10000")  # 10 * 1000
    assert vals[dt.date(2025, 2, 1)] == Decimal("5500")  # 5 * 1100 after the sell


def test_ledger_equity_wins_over_ecas_snapshot_no_double_count(
    make_investor, make_security, make_folio, make_transaction, make_holding
):
    """When a demat folio has both a tradebook ledger and an eCAS holding, the
    ledger is authoritative for units — headline and trend must not double-count."""
    inv = make_investor()
    demat = make_folio(investor=inv, folio_type=FolioType.DEMAT.value, number="1234567890123456")
    equity = make_security(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
        exchange="NSE",
    )
    make_transaction(
        investor=inv,
        security=equity,
        folio=demat,
        date=dt.date(2025, 1, 1),
        units=Decimal("10"),
        nav_or_price=Decimal("1000"),
    )
    make_holding(
        investor=inv,
        security=equity,
        folio=demat,
        as_of_date=dt.date(2025, 6, 1),
        units=Decimal("15"),
    )
    NAVHistory.objects.create(security=equity, date=dt.date(2025, 1, 1), nav=Decimal("1000"))

    valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))
    assert _values(inv)[dt.date(2025, 1, 1)] == Decimal("10000")  # 10 ledger, not 25
    summary = build_investor_summary(inv, dt.date(2025, 1, 1))
    assert summary["total_inr"] == Decimal("10000")


def test_period_returns_windowed_xirr(make_investor, make_security, make_folio, make_transaction):
    """Trailing returns: each window is a money-weighted XIRR over its own span.

    One MF bought at NAV 10 that doubles to 20 over a year (via a 15 midpoint):
    the lifetime window ≈ 100% p.a., the 6-month window's opening value is the
    midpoint (1500 → 2000 = +33% absolute), and windows predating the first
    transaction (3Y/5Y) are dropped rather than fabricated.
    """
    inv = make_investor()
    folio = make_folio(investor=inv, number="99999999")
    mf = make_security(security_type=SecurityType.MF.value, name="Bluechip", isin="INF000A00001")
    make_transaction(
        investor=inv,
        security=mf,
        folio=folio,
        date=dt.date(2024, 1, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
    )
    for on, nav in [
        (dt.date(2024, 1, 1), 10),
        (dt.date(2024, 7, 1), 15),
        (dt.date(2025, 1, 1), 20),
    ]:
        NAVHistory.objects.create(security=mf, date=on, nav=Decimal(nav))

    by_period = {
        r["period"]: r for r in compute_portfolio_period_returns([inv], dt.date(2025, 1, 1))
    }

    assert "3Y" not in by_period and "5Y" not in by_period  # younger than these windows
    assert by_period["All"]["annualized"] == pytest.approx(1.0, abs=0.03)  # value doubled in a year
    six_month = by_period["6M"]
    assert six_month["days"] == 184
    assert six_month["absolute"] == pytest.approx(0.3333, abs=0.01)  # 1500 → 2000


def test_partial_equity_excluded_from_series_but_snapshot_counts_headline(
    make_investor, make_security, make_folio, make_transaction, make_holding
):
    """Incomplete-history equity (cost_basis_complete=False) is excluded from the
    trend; an eCAS snapshot on the same folio still prices into headline net worth."""
    inv = make_investor()
    demat = make_folio(investor=inv, folio_type=FolioType.DEMAT.value, number="1234567890123456")
    equity = make_security(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
        exchange="NSE",
    )
    make_transaction(
        investor=inv,
        security=equity,
        folio=demat,
        date=dt.date(2025, 1, 1),
        transaction_type=TransactionType.SELL.value,
        units=Decimal("5"),
        nav_or_price=Decimal("1100"),
        cost_basis_complete=False,
    )
    make_holding(
        investor=inv,
        security=equity,
        folio=demat,
        as_of_date=dt.date(2025, 6, 1),
        units=Decimal("10"),
    )
    NAVHistory.objects.create(security=equity, date=dt.date(2025, 1, 1), nav=Decimal("1100"))

    valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))
    assert _values(inv)[dt.date(2025, 1, 1)] == Decimal("0")
    summary = build_investor_summary(inv, dt.date(2025, 1, 1))
    assert summary["total_inr"] == Decimal("11000")  # 10 snapshot units * 1100


def test_unpriced_equity_with_symbol_errors_then_recovers(
    make_investor, make_security, make_holding
):
    """An equity with a symbol but no price yet is feed-pending — error + retry,
    like an unpriced MF, until the price arrives. A snapshot-only equity then
    prices into headline net worth but not the day-wise series."""
    inv = make_investor()
    equity = make_security(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
        exchange="NSE",
    )
    make_holding(investor=inv, security=equity, as_of_date=dt.date(2025, 1, 1), units=Decimal("10"))

    assert valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1)) == (
        ValuationStatus.ERROR
    )
    inv.refresh_from_db()
    assert inv.valuation_next_attempt_at is not None
    assert not InvestorValue.objects.filter(investor=inv).exists()

    NAVHistory.objects.create(security=equity, date=dt.date(2025, 1, 1), nav=Decimal("1400"))
    inv.valuation_next_attempt_at = timezone.now() - dt.timedelta(minutes=1)
    inv.save(update_fields=["valuation_next_attempt_at"])
    assert valuation_jobs.process_pending_valuations() == 1
    inv.refresh_from_db()
    assert inv.valuation_status == ValuationStatus.READY
    assert _values(inv)[dt.date(2025, 1, 1)] == Decimal("0")  # snapshot-only: no trend
    assert build_investor_summary(inv, dt.date(2025, 1, 1))["total_inr"] == Decimal("14000")


def test_symbolless_equity_degrades_not_blocks(
    make_investor, make_security, make_folio, make_transaction, make_holding
):
    """An equity the ISIN DB couldn't map to a symbol is unmappable — degrade
    (fall out of the series) rather than error/retry forever. The rest of the
    portfolio still values and the investor stays READY."""
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

    unmapped = make_security(
        security_type=SecurityType.EQUITY.value,
        name="Unlisted Co",
        isin="INE000X00X00",
        symbol="",
        exchange="",
    )
    make_holding(
        investor=inv, security=unmapped, as_of_date=dt.date(2025, 1, 1), units=Decimal("5")
    )

    status = valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2025, 1, 1))
    assert status == ValuationStatus.READY  # degraded, not errored
    assert _values(inv)[dt.date(2025, 1, 1)] == Decimal("1000")  # only the priced MF
