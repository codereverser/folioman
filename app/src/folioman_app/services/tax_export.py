"""Schedule 112A export — bridges the core tax engine to the API, per investor.

Gating is **per (security, folio)**: only transactions from tax-ready folios reach
FIFO, so disposals (and the resulting 112A rows) can only come from buckets whose
integrity status is tax-ready. This is done in the app by pre-filtering the
ledger, which keeps the core tax engine folio-agnostic. India-only in v1.

FMV-as-of-31-Jan-2018 (grandfathering) comes from the layered ``services.fmv``
lookup (MF via casparser's NAV dataset, listed equity via backfilled price
history) — injectable so tests stay deterministic.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_HALF_EVEN, Decimal

from folioman_core.reconciliation import IntegrityStatus
from folioman_core.tax import compute_gain_lines, compute_schedule_112a, get_policy
from folioman_core.tax.india import india_fy_range
from folioman_core.tax.models import Term
from folioman_core.tax.schedule_112a import SCHEDULE_112A_CSV_COLUMNS

from folioman_app.models import Investor, Security
from folioman_app.services.fmv import fmv_lookup as _default_fmv
from folioman_app.services.projected_ledger import projected_transactions, security_key

_Q2 = Decimal("0.01")


def _folio_tax_ready(status: IntegrityStatus, *, include_unreconciled: bool) -> bool:
    """Per-folio mirror of the core export gate (schedule_112a._is_tax_ready)."""
    if status is IntegrityStatus.USER_ACKNOWLEDGED:
        return False
    if include_unreconciled:
        return status is not IntegrityStatus.SNAPSHOT_ONLY
    return status in (IntegrityStatus.FULL_HISTORY, IntegrityStatus.RECONCILED)


def _tax_ready_transactions(investor: Investor, *, include_unreconciled: bool) -> list:
    """Core transactions from this investor's tax-ready (security, folio) buckets.

    Per-folio gating: only buckets whose integrity status is tax-ready reach FIFO,
    so disposals can only come from those. Fails closed — a bucket with no status
    is simply absent from the ready set.
    """
    ready_keys = {
        (security_key(st.security), st.folio.number)
        for st in investor.integrity_statuses.select_related("security", "folio").all()
        if _folio_tax_ready(IntegrityStatus(st.status), include_unreconciled=include_unreconciled)
    }
    # Corporate-action-adjusted ledger (split-scaled units, merged lots, bonus shares):
    # FIFO over the projection gives the right cost basis and proceeds without rewriting
    # rows. Keyed by stable security identity + folio so a merged disposal is gated by —
    # and attributed to — the acquirer's tax-ready bucket.
    return [
        core
        for core in projected_transactions(investor)
        if (security_key(core.security), core.folio_number or "") in ready_keys
    ]


def build_capital_gains(
    investor: Investor,
    fy_label: str,
    *,
    include_unreconciled: bool = False,
    fmv_lookup: Callable | None = None,
) -> dict:
    """Realised capital gains for one India FY: per-disposal rows split into
    short-/long-term, plus STCG/LTCG totals. Listed equity and equity-oriented
    mutual funds with tax-ready folios; same gating as the 112A export.
    """
    fmv = fmv_lookup if fmv_lookup is not None else _default_fmv
    fy_start, fy_end = india_fy_range(fy_label)  # raises ValueError on a bad label
    transactions = _tax_ready_transactions(investor, include_unreconciled=include_unreconciled)
    gain_lines = compute_gain_lines(transactions, get_policy("IN"), fmv_lookup=fmv)

    in_fy = [g for g in gain_lines if fy_start <= g.disposal.sold_on <= fy_end]
    # Map core securities back to Django ids so rows can deep-link to the scheme.
    isin_to_id = dict(
        Security.objects.filter(
            isin__in={g.disposal.security.isin for g in in_fy if g.disposal.security.isin}
        ).values_list("isin", "id")
    )

    rows: list[dict] = []
    stcg = Decimal("0")
    ltcg = Decimal("0")
    exempt = Decimal("0")
    for line in in_fy:
        d = line.disposal
        sale_value = (d.units * d.sale_price_per_unit).quantize(_Q2, rounding=ROUND_HALF_EVEN)
        if line.term is Term.SHORT:
            stcg += line.gain
        elif line.term is Term.LONG:
            ltcg += line.gain
        else:  # Term.EXEMPT — e.g. a buyback (s.10(34A)); not chargeable to CG
            exempt += line.gain
        rows.append(
            {
                "security_id": isin_to_id.get(d.security.isin),
                "name": d.security.name,
                "isin": d.security.isin,
                "units": d.units,
                "sale_value": sale_value,
                # Total cost basis (acquisition cost + stamp duty), defined as
                # sale minus gain so the row ties out exactly; matches how CAMS/KFin
                # fold purchase stamp into cost.
                "cost": sale_value - line.gain,
                "gain": line.gain,
                "term": line.term.value,
                "acquired_on": d.acquired_on,
                "sold_on": d.sold_on,
                # A pre-2018 LTCG lot whose 31-Jan-2018 FMV couldn't be fetched: the
                # grandfathering benefit is missing, so cost is understated and the
                # gain (and any tax) is overstated. Surfaced so the user can tell.
                "grandfathering_unavailable": bool(line.metadata.get("grandfathering_unavailable")),
            }
        )

    # Long-term first, then by sale date — the order a reviewer reads.
    rows.sort(key=lambda r: (r["term"], r["sold_on"]))
    return {
        "fy": fy_label,
        "stcg_total": stcg.quantize(_Q2, rounding=ROUND_HALF_EVEN),
        "ltcg_total": ltcg.quantize(_Q2, rounding=ROUND_HALF_EVEN),
        "exempt_total": exempt.quantize(_Q2, rounding=ROUND_HALF_EVEN),
        "rows": rows,
    }


def build_schedule_112a(
    investor: Investor,
    fy_label: str,
    *,
    include_unreconciled: bool = False,
    fmv_lookup: Callable | None = None,
) -> dict:
    fmv = fmv_lookup if fmv_lookup is not None else _default_fmv

    # Only tax-ready (security, folio) buckets reach FIFO, so disposals come only
    # from them (shared with the realised capital-gains view).
    transactions = _tax_ready_transactions(investor, include_unreconciled=include_unreconciled)
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
