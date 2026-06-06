"""Day-wise portfolio valuation jobs (driven by the background scheduler).

The investor row is the durable work-list: ``valuation_status`` + the
``valuation_*`` fields. ``recompute_investor_valuation`` ensures NAVs are present
for the investor's securities, then recomputes the persisted daily ``InvestorValue``
series from a start date to today. ``process_pending_valuations`` (the interval
tick) picks up investors that are pending/computing or a due-for-retry error;
``enqueue_daily_extend`` (the daily tick) rolls every series forward and re-queues
errored ones.

Transient feed failures (mfapi 502/SSL timeouts) leave the investor ``error`` with
``next_attempt_at`` set; the next tick retries with backoff, capped at
``_MAX_ATTEMPTS`` (terminal until the daily tick or a re-import re-queues it).

Scaling to distributed workers (deferred — do not build until a Postgres hosted
deployment actually needs concurrent recompute)
------------------------------------------------------------------------------
Swapping the *clock* (APScheduler -> Celery Beat / OS cron / k8s CronJob) is cheap
and already supported: external schedulers call the one-shot management commands
(``valuation_tick_pending`` / ``valuation_tick_daily_extend``), which run the same
ticks. **That alone does NOT make recompute safe to parallelise.** ``process_pending
_valuations`` selects due investors with an unguarded ``SELECT`` then loops, so two
workers would claim and recompute the same investor. Today a single in-thread tick
(APScheduler ``max_instances=1`` + ``coalesce``) hides this; cron/k8s triggers do
not bound overlap on their own. The real work to go distributed, in order:

1. **Atomic claim.** Replace the unguarded select with a row-claim: Postgres
   ``select_for_update(skip_locked=True)`` (or ``UPDATE ... RETURNING``) and lease
   columns (e.g. ``valuation_claimed_by`` / ``valuation_claimed_until``) so each
   due investor is owned by exactly one worker and abandoned leases expire.
2. **Disambiguate status.** ``COMPUTING`` currently means both "queued" (set by
   ``queue_recompute`` at import) and "running" (set at the top of
   ``recompute_investor_valuation``). Split into queued vs running before fan-out.
3. **Then fan out.** A tick claims N due investors; each worker runs
   ``recompute_investor_valuation(investor_id, from_date)`` (already idempotent via
   delete->=from_date + bulk_create). No ``Dispatcher``/broker abstraction is needed
   until a concrete worker runtime exists.
4. **SQLite/desktop stays serial** — ``skip_locked``/lease claiming is Postgres-only;
   the desktop single-worker path is unchanged.

Gotchas to carry into that work: cron/k8s lack ``max_instances`` (until the claim
lands, add an advisory lock / ``flock``), and the daily tick's timezone moves
outside Django config when scheduled by the OS rather than APScheduler.
"""

from __future__ import annotations

import datetime as dt
from datetime import timedelta
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import F, Min, Q
from django.utils import timezone
from folioman_core.models import SecurityType

from folioman_app.models import Investor, InvestorValue, NAVHistory, Security, ValuationStatus
from folioman_app.services.valuation import _value_series
from folioman_app.tasks.refresh_navs import backfill_missing_history, refresh_navs

_MAX_ATTEMPTS = 8

# The persisted ``InvestorValue`` series is always **daily** — one point per calendar
# day from the start date to today. The upsert in ``_upsert_series`` relies on this:
# daily sampling covers every date in ``[start, today]``, so each existing row in that
# window is overwritten and none is left orphaned (a sparser granularity would strand
# stale rows the old delete-then-rebuild used to sweep). Charts can downsample on read;
# the stored series stays daily-contiguous. Do not change without making the upsert
# sweep dates no longer in the computed set (see test_recompute_leaves_no_orphan_rows).
_SERIES_GRANULARITY = "daily"


def queue_recompute(
    investor: Investor,
    recompute_from: dt.date,
    *,
    provisional_value: Decimal | None = None,
    provisional_invested: Decimal | None = None,
    as_of: dt.date | None = None,
) -> None:
    """Mark an investor for day-wise recompute from ``recompute_from`` (extended
    back to cover any pending earlier date), set status ``computing``, and — when a
    statement value is supplied — seed one **provisional** ``InvestorValue`` at
    ``as_of`` so the headline/chart show a real number immediately, until the worker
    computes the precise live-NAV series and supersedes it. Call from the import.
    """
    existing = investor.valuation_recompute_from
    investor.valuation_recompute_from = (
        min(existing, recompute_from) if existing else recompute_from
    )
    investor.valuation_status = ValuationStatus.COMPUTING
    investor.save(update_fields=["valuation_recompute_from", "valuation_status", "updated_at"])
    if as_of is not None and provisional_value is not None and provisional_value > 0:
        InvestorValue.objects.update_or_create(
            investor=investor,
            date=as_of,
            defaults={
                "value_inr": provisional_value,
                "invested_inr": provisional_invested or Decimal("0"),
                "is_provisional": True,
            },
        )


def _held_security_ids(investor: Investor) -> set[int]:
    """Every security the investor has ever transacted in or holds (a fully-sold
    fund still needs historical NAVs to price the days it was held)."""
    return set(investor.transactions.values_list("security_id", flat=True)) | set(
        investor.holdings.values_list("security_id", flat=True)
    )


def _earliest_activity(investor: Investor) -> dt.date | None:
    txn = investor.transactions.aggregate(d=Min("date"))["d"]
    hold = investor.holdings.aggregate(d=Min("as_of_date"))["d"]
    dates = [d for d in (txn, hold) if d is not None]
    return min(dates) if dates else None


def _unpriced_securities(security_ids: set[int], as_of: dt.date) -> set[int]:
    """Securities with **no** NAV on/before ``as_of`` — i.e. their feed hasn't
    produced anything yet (the transient-failure case worth retrying)."""
    if not security_ids:
        return set()
    priced = set(
        NAVHistory.objects.filter(security_id__in=security_ids, date__lte=as_of).values_list(
            "security_id", flat=True
        )
    )
    return security_ids - priced


def _backoff(attempts: int) -> timedelta:
    return timedelta(minutes=min(2**attempts, 60))  # 1,2,4,…,60 min, capped


def _mark_ready(inv: Investor, through: dt.date) -> None:
    inv.valuation_status = ValuationStatus.READY
    inv.valuation_computed_through = through
    inv.valuation_recompute_from = None
    inv.valuation_next_attempt_at = None
    inv.valuation_attempts = 0
    inv.valuation_error = ""
    inv.save(
        update_fields=[
            "valuation_status",
            "valuation_computed_through",
            "valuation_recompute_from",
            "valuation_next_attempt_at",
            "valuation_attempts",
            "valuation_error",
            "updated_at",
        ]
    )


def _mark_error(inv: Investor, message: str) -> None:
    inv.valuation_attempts = (inv.valuation_attempts or 0) + 1
    inv.valuation_status = ValuationStatus.ERROR
    inv.valuation_error = message[:500]
    # Below the cap, schedule a retry; at/above it, stop auto-retrying (the daily
    # tick re-queues errored investors so a recovered feed still gets picked up).
    inv.valuation_next_attempt_at = (
        timezone.now() + _backoff(inv.valuation_attempts)
        if inv.valuation_attempts < _MAX_ATTEMPTS
        else None
    )
    inv.save(
        update_fields=[
            "valuation_status",
            "valuation_attempts",
            "valuation_error",
            "valuation_next_attempt_at",
            "updated_at",
        ]
    )


def _upsert_series(inv: Investor, points: list[dict]) -> None:
    """Upsert the freshly-computed daily ``points`` into the investor's
    ``InvestorValue`` series, keyed on (investor, date).

    Compute-then-upsert: the caller computes ``points`` *first*, so this only runs once
    a full new series is in hand. There is no destructive delete — every row is inserted
    or updated in place, so a failure here can never blank the investor; the prior (and
    any provisional) series survives and the attempt is left retryable. Provisional rows
    are superseded because ``is_provisional`` is in ``update_fields`` (the conflict path
    resets it to False). One ``atomic()`` so a large series batched across statements is
    seen all-or-nothing by a concurrent reader. Empty ``points`` is a no-op.

    Relies on the series being daily-contiguous (see ``_SERIES_GRANULARITY``): daily
    sampling covers every date in the recomputed window, so no prior row in range is
    left stale. With a sparser granularity this would need to also delete dates no
    longer in the computed set."""
    if not points:
        return
    with db_transaction.atomic():
        InvestorValue.objects.bulk_create(
            [
                InvestorValue(
                    investor=inv,
                    date=p["date"],
                    invested_inr=p["invested_inr"],
                    value_inr=p["value_inr"],
                    is_provisional=False,
                )
                for p in points
            ],
            update_conflicts=True,
            unique_fields=["investor", "date"],
            update_fields=["invested_inr", "value_inr", "is_provisional", "updated_at"],
        )


def recompute_investor_valuation(
    investor_id: int, from_date: dt.date | None = None, *, prime_navs: bool = True
) -> str:
    """Recompute the daily ``InvestorValue`` series for one investor from
    ``from_date`` (or its pending/last-computed/earliest date) to today. Returns the
    resulting status string. Best-effort: feed failures leave it retryable.

    ``prime_navs`` (default True) refreshes current NAVs + backfills MF history for
    this investor's holdings before valuing. Batch callers that already primed the
    NAV cache once for *all* due investors (see ``process_pending_valuations``) pass
    ``False`` so shared schemes aren't re-fetched per investor — the cause of
    feed rate-limiting at scale."""
    inv = Investor.objects.filter(id=investor_id).first()
    if inv is None:
        return "missing"
    today = timezone.localdate()
    start = (
        from_date
        or inv.valuation_recompute_from
        or inv.valuation_computed_through
        or _earliest_activity(inv)
    )
    if start is None:  # no holdings/transactions yet → nothing to value
        _mark_ready(inv, today)
        return ValuationStatus.READY

    inv.valuation_status = ValuationStatus.COMPUTING
    inv.save(update_fields=["valuation_status", "updated_at"])
    try:
        sec_ids = _held_security_ids(inv)
        securities = Security.objects.filter(id__in=sec_ids)
        if prime_navs:
            refresh_navs(securities=securities)  # current NAV per security
            backfill_missing_history(  # MF history (mfapi); equities keep latest only
                securities=securities.filter(security_type=SecurityType.MF.value)
            )
        # Only MF securities have a history feed. An unpriced MF splits two ways:
        #  - **feed-pending** (has an amfi_code → the feed should price it): a slow
        #    or transient feed; keep erroring + retrying with backoff so it recovers.
        #  - **unpriceable** (no amfi_code → nothing to query: an eCAS demat fund the
        #    ISIN DB can't map, or a closed/matured scheme): retrying never helps, so
        #    it must NOT block the whole investor.
        # Degrade per-security: value the priceable holdings and let the unpriced ones
        # fall out of the series (``_value_series`` skips + flags them; they surface as
        # ``stale_count`` in the summary) — never blank an otherwise-valued portfolio.
        unpriced = _unpriced_securities(
            {s.id for s in securities if s.security_type == SecurityType.MF.value}, today
        )
        if unpriced:
            no_code = set(
                Security.objects.filter(id__in=unpriced, amfi_code="").values_list("id", flat=True)
            )
            pending = unpriced - no_code
            if pending and inv.valuation_attempts < _MAX_ATTEMPTS:
                _mark_error(inv, f"{len(pending)} securities awaiting NAV (feed pending)")
                return ValuationStatus.ERROR
            # else: only structurally-unpriceable left (or retries exhausted) — degrade.

        # Compute the full new series first, then upsert it — a failure in either step
        # leaves the prior/provisional series intact (see _upsert_series).
        points = _value_series([inv], start, today, _SERIES_GRANULARITY)
        _upsert_series(inv, points)
        _mark_ready(inv, today)
        return ValuationStatus.READY
    except Exception as exc:
        _mark_error(inv, f"{type(exc).__name__}: {exc}")
        return ValuationStatus.ERROR


def process_pending_valuations() -> int:
    """Interval tick: recompute every investor that's pending/computing, or an error
    whose backoff has elapsed. Returns how many were processed."""
    now = timezone.now()
    due = list(
        Investor.objects.filter(
            Q(valuation_status__in=[ValuationStatus.PENDING, ValuationStatus.COMPUTING])
            | Q(valuation_status=ValuationStatus.ERROR, valuation_next_attempt_at__lte=now)
        ).values_list("id", "valuation_recompute_from")
    )
    if not due:
        return 0
    # Prime the NAV cache ONCE for the union of all due investors' holdings, then
    # value each off the cache (prime_navs=False). Without this, investors sharing
    # popular schemes each re-fetch them — the burst that rate-limits the feed.
    investor_ids = [iid for iid, _ in due]
    sec_ids: set[int] = set()
    for inv in Investor.objects.filter(id__in=investor_ids):
        sec_ids.update(_held_security_ids(inv))
    if sec_ids:
        securities = Security.objects.filter(id__in=sec_ids)
        refresh_navs(securities=securities)
        backfill_missing_history(securities=securities.filter(security_type=SecurityType.MF.value))
    processed = 0
    for investor_id, recompute_from in due:
        recompute_investor_valuation(investor_id, recompute_from, prime_navs=False)
        processed += 1
    return processed


def enqueue_daily_extend() -> int:
    """Daily tick: roll every ready series forward to today, and re-queue errored
    investors (so a recovered feed gets retried). Returns how many were queued."""
    today = timezone.localdate()
    rolled = Investor.objects.filter(
        valuation_status=ValuationStatus.READY, valuation_computed_through__lt=today
    ).update(
        valuation_status=ValuationStatus.PENDING,
        # re-do the last computed day (catches late NAVs) and extend to today
        valuation_recompute_from=F("valuation_computed_through"),
    )
    retried = Investor.objects.filter(valuation_status=ValuationStatus.ERROR).update(
        valuation_status=ValuationStatus.PENDING,
        valuation_attempts=0,
        valuation_next_attempt_at=None,
    )
    return rolled + retried


def enqueue_catch_up_if_stale() -> int:
    """Launch-time catch-up. The 02:00 ``enqueue_daily_extend`` cron only fires while
    a scheduler is running, so a desktop app that was closed overnight (or a process
    asleep across 02:00) leaves READY investors at their last-computed day with no
    PENDING flag for the interval tick to act on. On scheduler start, if any READY
    series is behind today, run the daily extend **once** so the next interval tick
    brings everyone current. Idempotent and quiet: a no-op (returns 0) when every
    READY series is already at today — the same flag-then-tick path, just kicked at
    startup, so no duplicate work if current."""
    today = timezone.localdate()
    behind = Investor.objects.filter(
        valuation_status=ValuationStatus.READY, valuation_computed_through__lt=today
    ).exists()
    if not behind:
        return 0
    return enqueue_daily_extend()
