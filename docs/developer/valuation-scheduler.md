# Day-wise valuation scheduler

How the background day-wise portfolio valuation is triggered, and how to move
that trigger off the in-process scheduler when a deployment grows.

## The two seams

The valuation worker has two independent layers:

- **Job logic** (`app/tasks/valuation_jobs.py`) — plain, scheduler-free functions
  that operate on a durable DB work-list (`Investor.valuation_*` status fields).
  `process_pending_valuations()` handles pending/retryable investors;
  `enqueue_daily_extend()` rolls ready series forward.
- **Trigger** — *what wakes the job up*. This is a replaceable convention, not a
  class hierarchy. Every trigger calls the same scheduler-neutral entrypoints in
  `app/tasks/valuation_ticks.py` (`run_pending_valuations_tick()` /
  `run_daily_extend_tick()`), which add connection hygiene and exception
  containment around the job functions.

`scheduler.py` is just an APScheduler **adapter** — a small job registry it
translates into APScheduler interval/cron jobs. APScheduler is one trigger
provider, not the owner of valuation state.

## Trigger options

Run **exactly one** trigger source per environment.

| Mode | Trigger |
|------|---------|
| Desktop | In-process APScheduler thread, started from `AppConfig.ready()` when `FOLIOMAN_RUN_SCHEDULER` is on. |
| Small server | One dedicated `manage.py run_scheduler` process beside gunicorn (never one per worker). |
| Larger / external | Disable `run_scheduler`; have OS cron, a systemd timer, a Kubernetes CronJob, or a Celery Beat shell task invoke the one-shot commands below. |

One-shot, broker-free commands (no APScheduler, pure-Python — safe for the
desktop build and for any external scheduler):

```bash
python manage.py valuation_tick_pending          # one pending-valuation pass
python manage.py valuation_tick_daily_extend      # one daily roll-forward pass
```

Example external schedule (cron): pending every minute, daily extend at 02:00.

```cron
* * * * *  /path/to/venv/bin/python /app/manage.py valuation_tick_pending
0 2 * * *  /path/to/venv/bin/python /app/manage.py valuation_tick_daily_extend
```

## Scaling to distributed workers (not built yet)

Swapping the clock is the easy part. It does **not** make recompute safe to run
on multiple workers in parallel — `process_pending_valuations()` selects due
investors with an unguarded query and loops, so two workers would recompute the
same investor. A single in-thread APScheduler tick (`max_instances=1` +
`coalesce`) hides this today; cron/k8s triggers do not bound overlap on their
own.

When a Postgres hosted deployment actually needs concurrent recompute, the work,
in order, is:

1. **Atomic claim** — replace the unguarded select with a row-claim
   (`select_for_update(skip_locked=True)` or `UPDATE … RETURNING`) plus lease
   columns (`valuation_claimed_by` / `valuation_claimed_until`) so each investor
   is owned by exactly one worker and abandoned leases expire.
2. **Disambiguate status** — `COMPUTING` currently means both "queued" and
   "running"; split it before fan-out.
3. **Fan out** — a tick claims N due investors; each worker runs
   `recompute_investor_valuation(...)`, already idempotent (delete-≥-from-date
   then bulk-create). No broker/`Dispatcher` abstraction is needed until a
   concrete worker runtime exists.
4. **SQLite/desktop stays serial** — the claim path is Postgres-only.

The trigger swap is cheap; the atomic claim is the real work. Until it lands,
keep one trigger source per environment (add an advisory lock / `flock` if you
must run a cron trigger that could overlap). Note that scheduling the daily tick
via the OS moves its timezone out of Django config.
