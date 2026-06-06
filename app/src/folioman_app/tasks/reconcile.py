"""Reconcile an investor's ledger against holding snapshots, per (security, folio).

Reconciliation is per-folio because completeness and cost basis are per-folio: a
scheme can be full-history in one folio and snapshot-only in another, and a sell
consumes only its own folio's lots. Delegates the per-folio comparison to
``folioman_core.reconciliation.reconcile`` (single source of truth) and upserts a
``SecurityIntegrityStatus`` row per (investor, security, folio). A prior
USER_ACKNOWLEDGED mismatch is preserved across re-reconciles. The integrity
router builds list/acknowledge endpoints on top of this.
"""

from __future__ import annotations

from django.utils import timezone
from folioman_core.models import HoldingSource
from folioman_core.reconciliation import IntegrityStatus, reconcile

from folioman_app.mappers import to_core_holding, to_core_transaction
from folioman_app.models import Folio, Investor, Security, SecurityIntegrityStatus


def reconcile_security_folio(
    investor: Investor,
    security: Security,
    folio: Folio,
    *,
    acknowledge: bool = False,
    clear_acknowledgement: bool = False,
) -> SecurityIntegrityStatus | None:
    """Reconcile one (investor, security, folio) and upsert its status.

    ``acknowledge=True`` forces user-acknowledgement of a current mismatch (the
    explicit "I accept this gap" action); a prior acknowledgement is preserved
    on re-reconcile regardless. ``clear_acknowledgement=True`` undoes that — it
    drops a prior acknowledgement so the row reverts to its real status (a
    still-unresolved gap reappears as a mismatch). The two are mutually
    exclusive; ``clear_acknowledgement`` wins if both are set.
    """
    # Cost-basis rows only: a partial-history folio has none here, so it reconciles
    # as snapshot-only (its closing-balance holding vs an empty ledger) — never a
    # spurious MISMATCH from partial units.
    txns = [
        to_core_transaction(t)
        for t in investor.transactions.cost_basis()
        .filter(security=security, folio=folio)
        .select_related("security", "folio")
    ]
    holdings = [
        to_core_holding(h)
        for h in investor.holdings.filter(security=security, folio=folio).select_related(
            "security", "folio"
        )
    ]
    # A cas-pdf snapshot is the closing balance of a CAS scheme. Relative to a
    # ledger it's either stale or a fresh check, decided by date:
    #  - a snapshot at/before the ledger's latest transaction is a superseded
    #    self-snapshot (e.g. the partial import that a since-inception import then
    #    completed) — drop it so the upgrade isn't dragged to a spurious MISMATCH;
    #  - a snapshot *after* the latest transaction is a newer statement's reported
    #    close — keep it, so a stale/gappy ledger that disagrees surfaces as MISMATCH.
    # (eCAS holdings are always independent observations and are never dropped.)
    if txns:
        latest_txn = max(t.date for t in txns)
        holdings = [
            h
            for h in holdings
            if not (h.source is HoldingSource.CAS_PDF and h.as_of_date <= latest_txn)
        ]

    existing = SecurityIntegrityStatus.objects.filter(
        investor=investor, security=security, folio=folio
    ).first()
    already_acknowledged = bool(
        existing and existing.status == IntegrityStatus.USER_ACKNOWLEDGED.value
    )
    user_acknowledged = False if clear_acknowledgement else (already_acknowledged or acknowledge)

    result = reconcile(txns or None, holdings or None, user_acknowledged=user_acknowledged)

    if result is None:
        # Nothing to reconcile in this folio — drop any stale status.
        if existing:
            existing.delete()
        return None

    # Temporal context for the comparison: how far each side's evidence reaches.
    ledger_through = max((t.date for t in txns), default=None)
    snapshot_as_of = max((h.as_of_date for h in holdings), default=None)

    status, _ = SecurityIntegrityStatus.objects.update_or_create(
        investor=investor,
        security=security,
        folio=folio,
        defaults={
            "status": result.status.value,
            "tax_safe": result.tax_safe,
            "units_from_transactions": result.units_from_transactions,
            "units_from_holdings": result.units_from_holdings,
            "issues": result.issues,
            "ledger_through": ledger_through,
            "snapshot_as_of": snapshot_as_of,
            "last_reconciled_at": timezone.now(),
        },
    )
    return status


def reconcile_security(investor: Investor, security: Security) -> list[SecurityIntegrityStatus]:
    """Reconcile every folio that holds this security; prune stale folio statuses."""
    folio_ids = set(
        investor.transactions.filter(security=security).values_list("folio_id", flat=True)
    )
    folio_ids |= set(investor.holdings.filter(security=security).values_list("folio_id", flat=True))
    folio_ids.discard(None)

    statuses: list[SecurityIntegrityStatus] = []
    for folio in Folio.objects.filter(id__in=folio_ids):
        status = reconcile_security_folio(investor, security, folio)
        if status is not None:
            statuses.append(status)

    # Drop statuses for (security, folio) pairs that no longer have data.
    SecurityIntegrityStatus.objects.filter(investor=investor, security=security).exclude(
        folio_id__in=folio_ids
    ).delete()
    return statuses


def reconcile_after_import(investor: Investor, securities) -> list[dict]:
    """Reconcile each affected security (all its folios) after an import committed.

    A reconcile failure must NOT discard the imported data (it's expensive to
    re-parse a CAS PDF). Each security is reconciled in isolation; failures are
    collected and returned so the import job can be marked
    ``COMPLETED_WITH_WARNINGS`` and the user can retry reconcile rather than the
    whole import. The affected security simply stays unreconciled — and, because
    the 112A export fails closed on a missing/non-ready status, it is correctly
    kept out of any tax filing until reconcile succeeds.
    """
    errors: list[dict] = []
    for security in securities:
        try:
            reconcile_security(investor, security)
        except Exception as exc:  # isolate one security's failure from the rest
            errors.append(
                {"security_id": security.id, "security": security.name, "error": str(exc)}
            )
    return errors


def recompute_investor(investor: Investor) -> list[SecurityIntegrityStatus]:
    """Re-reconcile every security the investor has transactions or holdings for."""
    security_ids = set(investor.transactions.values_list("security_id", flat=True))
    security_ids |= set(investor.holdings.values_list("security_id", flat=True))
    statuses: list[SecurityIntegrityStatus] = []
    for security in Security.objects.filter(id__in=security_ids):
        statuses.extend(reconcile_security(investor, security))
    return statuses


def reconcile_all_investors() -> dict:
    """Recompute integrity for every investor (e.g. after a reconcile-logic change)."""
    investors = 0
    statuses = 0
    for investor in Investor.objects.all():
        investors += 1
        statuses += len(recompute_investor(investor))
    return {"investors": investors, "statuses": statuses}
