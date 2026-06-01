"""Schedule 112A export — bridges the core tax engine to the API, per investor.

Gating is **per (security, folio)**: only transactions from tax-ready folios reach
FIFO, so disposals (and the resulting 112A rows) can only come from buckets whose
integrity status is tax-ready. This is done in the app by pre-filtering the
ledger, which keeps the core tax engine folio-agnostic. India-only in v1.

FMV-as-of-31-Jan-2018 (grandfathering) comes from casparser-isin via
``casparser_fmv.fmv_lookup`` — injectable so tests stay deterministic.
"""

from __future__ import annotations

from collections.abc import Callable

from folioman_core.price_feeds.casparser_fmv import fmv_lookup as _default_fmv
from folioman_core.reconciliation import IntegrityStatus
from folioman_core.tax import compute_gain_lines, compute_schedule_112a, get_policy
from folioman_core.tax.schedule_112a import SCHEDULE_112A_CSV_COLUMNS

from folioman_app.mappers import to_core_transaction
from folioman_app.models import Investor


def _folio_tax_ready(status: IntegrityStatus, *, include_unreconciled: bool) -> bool:
    """Per-folio mirror of the core export gate (schedule_112a._is_tax_ready)."""
    if status is IntegrityStatus.USER_ACKNOWLEDGED:
        return False
    if include_unreconciled:
        return status is not IntegrityStatus.SNAPSHOT_ONLY
    return status in (IntegrityStatus.FULL_HISTORY, IntegrityStatus.RECONCILED)


def build_schedule_112a(
    investor: Investor,
    fy_label: str,
    *,
    include_unreconciled: bool = False,
    fmv_lookup: Callable | None = None,
) -> dict:
    fmv = fmv_lookup if fmv_lookup is not None else _default_fmv

    # Which (security, folio) buckets are tax-ready? (Fails closed: a bucket with
    # no integrity status simply isn't in this set, so it can't be exported.)
    ready_keys = {
        (st.security_id, st.folio.number)
        for st in investor.integrity_statuses.select_related("folio").all()
        if _folio_tax_ready(IntegrityStatus(st.status), include_unreconciled=include_unreconciled)
    }
    # Only ready folios' transactions reach FIFO, so disposals come only from
    # tax-ready (security, folio) buckets.
    transactions = [
        to_core_transaction(t)
        for t in investor.transactions.select_related("security", "folio").all()
        if (t.security_id, t.folio.number if t.folio else "") in ready_keys
    ]
    gain_lines = compute_gain_lines(transactions, get_policy("IN"), fmv_lookup=fmv)

    # Per-folio gating is already applied above; mark the surviving securities
    # ready so the core per-security gate (which can't see folios) lets them through.
    integrity_by_security = {t.security: IntegrityStatus.RECONCILED for t in transactions}
    rows = compute_schedule_112a(
        gain_lines,
        fy_label,
        include_unreconciled=False,
        integrity_by_security=integrity_by_security,
        fmv_lookup=fmv,
    )

    return {
        "fy": fy_label,
        "include_unreconciled": include_unreconciled,
        "row_count": len(rows),
        "columns": list(SCHEDULE_112A_CSV_COLUMNS),
        "rows": [row.to_csv_dict() for row in rows],
    }
