"""Jobs & valuation-status overview for the Settings panel.

Read-only aggregation across an advisor's investors: the recent import jobs (what
ran, when, the outcome) and each investor's day-wise valuation status with the
**real** underlying cause of any failure — not the generic "N securities awaiting
NAV", but per-security: a structurally unmapped ISIN, a closed/matured scheme, or a
genuinely feed-pending fetch. This is what makes a failure actionable (re-download a
complete CAS vs wait for the feed vs nothing-to-do).
"""

from __future__ import annotations

from datetime import date

from folioman_core.models import SecurityType

from folioman_app.models import ImportJob, Investor, NAVHistory, Security

# How many recent import jobs to surface across all the advisor's investors.
_RECENT_IMPORTS = 25

# Why a held mutual fund has no usable NAV — the actionable cause behind the generic
# "feed pending" the investor row records. Mirrors the classification the valuation
# recompute uses to decide error-and-retry (feed_pending) vs degrade (closed/unmapped).
_CAUSE_DETAIL = {
    "closed": (
        "Closed or matured — the NAV feed has no data for this scheme; "
        "valued at its last-known figure."
    ),
    "unmapped": "Unmapped — its ISIN isn't in the AMFI code map, so there's no feed to query.",
    "feed_pending": (
        "Awaiting NAV — the feed hasn't returned a price yet "
        "(slow or temporarily down); it will retry."
    ),
}


def _identifier(security: Security) -> str:
    return security.isin or security.amfi_code or security.symbol or ""


def _classify(security: Security) -> str:
    """Why this held MF can't be priced — the durable cause, same split the recompute
    uses: a confirmed-dead feed code (closed), no code to query (unmapped), or a code
    the feed simply hasn't answered for yet (feed_pending, transient)."""
    if security.nav_feed_closed:
        return "closed"
    if not security.amfi_code:
        return "unmapped"
    return "feed_pending"


def build_valuation_diagnostics(investor: Investor, *, as_of: date | None = None) -> dict:
    """One investor's valuation status plus the per-security cause of any unpriced MF
    holding — the actionable detail behind ``valuation_error``."""
    today = as_of or date.today()
    sec_ids = set(investor.transactions.values_list("security_id", flat=True)) | set(
        investor.holdings.values_list("security_id", flat=True)
    )
    mf = list(Security.objects.filter(id__in=sec_ids, security_type=SecurityType.MF.value))
    priced = set(
        NAVHistory.objects.filter(security_id__in=[s.id for s in mf], date__lte=today).values_list(
            "security_id", flat=True
        )
    )
    issues = [
        {
            "security_id": s.id,
            "security_name": s.name,
            "identifier": _identifier(s),
            "cause": (cause := _classify(s)),
            "detail": _CAUSE_DETAIL[cause],
        }
        for s in mf
        if s.id not in priced  # only the held MFs with no NAV on/before today
    ]
    issues.sort(key=lambda i: (i["cause"], i["security_name"]))
    return {
        "investor_id": investor.id,
        "investor_name": investor.name,
        "status": investor.valuation_status,
        "computed_through": investor.valuation_computed_through,
        "error": investor.valuation_error,
        "attempts": investor.valuation_attempts,
        "next_attempt_at": investor.valuation_next_attempt_at,
        "issues": issues,
    }


def build_jobs_overview(investors) -> dict:
    """Advisor-wide overview: recent import jobs (newest first, across all the
    advisor's investors) + each investor's valuation diagnostics."""
    investors = list(investors)
    names = {inv.id: inv.name for inv in investors}
    # ImportJob.Meta orders by -created_at, so the slice is the most recent jobs.
    jobs = ImportJob.objects.filter(investor_id__in=names)[:_RECENT_IMPORTS]
    imports = [
        {
            "id": j.id,
            "investor_id": j.investor_id,
            "investor_name": names.get(j.investor_id, ""),
            "kind": j.kind,
            "status": j.status,
            "filename": j.filename,
            "error": j.error,
            "result": j.result,
            "created_at": j.created_at,
            "finished_at": j.finished_at,
        }
        for j in jobs
    ]
    valuations = [build_valuation_diagnostics(inv, as_of=date.today()) for inv in investors]
    return {"imports": imports, "valuations": valuations}
