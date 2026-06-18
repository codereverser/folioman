"""Scheduler-neutral entrypoints for the NSE/BSE corporate-action refresh.

Same contract as ``isin_ticks`` / ``valuation_ticks``: thin, exception-contained
wrappers any trigger can call — the in-process APScheduler (desktop), the
``run_scheduler`` process (server), or the ``refresh_corporate_actions`` management
command. No ``apscheduler`` import, so it stays usable from a bare ``manage.py``
process and the pure-Python desktop build.

These keep the corporate-action cache populated automatically (daily + on launch)
for the equities that actually need it — the ones currently in a unit mismatch —
so the integrity page can offer real, event-by-event suggestions instead of
leaving the user to hand-author a guessed action against an empty cache.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _run(fn: Callable[[], dict]) -> int:
    """Contain any exception (return 0) so a recurring trigger keeps ticking."""
    try:
        return fn().get("events", 0)
    except Exception:
        logger.exception("corporate-action tick %s failed", getattr(fn, "__name__", fn))
        return 0


def run_corporate_action_refresh_tick() -> int:
    """Daily: refresh cached NSE/BSE corporate actions for the actionable equities."""
    from folioman_app.tasks.refresh_corporate_actions import (
        refresh_corporate_actions_for_mismatches,
    )

    return _run(refresh_corporate_actions_for_mismatches)


# Launch catch-up reuses the same scope — a daily run skipped while the desktop app
# was shut still fetches the actionable equities on next start.
run_corporate_action_catch_up_tick = run_corporate_action_refresh_tick
