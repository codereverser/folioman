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

import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from folioman_core.corporate_action_detect import (
    CachedCorporateAction,
    ReplayMatch,
    ReplayStep,
    detect_corporate_action_issues,
    strip_corporate_action_issues,
)
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.corporate_actions import (
    CorporateActionApplyEvent,
    apply_corporate_action_events,
    held_units_asof,
)
from folioman_core.fifo import net_units_from_transactions
from folioman_core.models import HoldingSource, SecurityType
from folioman_core.reconciliation import TOLERANCE, IntegrityStatus, ReconciliationResult, reconcile

from folioman_app.mappers import to_core_holding, to_core_security, to_core_transaction
from folioman_app.models import (
    CorporateActionReference,
    Folio,
    Investor,
    PartialBlock,
    Security,
    SecurityIntegrityStatus,
)
from folioman_app.services.dividends import attribute_dividends_for_security
from folioman_app.services.projected_ledger import compute_ledger

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")


def _incomplete_history_issue(partial: PartialBlock) -> dict:
    return {
        "type": "incomplete_history",
        "reason": "orphan_sell",
        "missing_prior_units": str(partial.opening_units),
    }


def _annotate_incomplete_history(
    result: ReconciliationResult,
    *,
    partial: PartialBlock,
) -> ReconciliationResult:
    """Tag a non-tax-ready folio whose ledger has orphan sells / missing buys.

    Leaves ``units_from_transactions`` as the reconcile set it (``None`` — the
    cost-basis ledger is empty here): the raw net of a mid-history ledger isn't a
    real holding (it omits the missing prior buys and can even go negative), so we
    don't surface it. ``missing_prior_units`` on the issue carries the real story.
    """
    issues = [i for i in result.issues if i.get("type") != "incomplete_history"]
    issues.insert(0, _incomplete_history_issue(partial))
    return result.model_copy(update={"tax_safe": False, "issues": issues})


def _cached_actions_for_security(security: Security) -> list[CachedCorporateAction]:
    """Load cached feed rows for ``security`` (ISIN-first, then symbol fallback)."""
    if security.security_type != SecurityType.EQUITY.value:
        return []
    filt = Q(security=security)
    if security.isin:
        filt |= Q(isin=security.isin)
    rows: dict[int, CachedCorporateAction] = {}
    for row in CorporateActionReference.objects.filter(filt).distinct():
        rows[row.id] = CachedCorporateAction(
            ex_date=row.ex_date,
            subject=row.subject,
            parsed_type=row.parsed_type,
            unit_multiplier=row.unit_multiplier,
            needs_review=row.needs_review,
            reference_id=row.id,
            bonus_ratio=_parsed_ratio(row),
        )
    return list(rows.values())


def _parsed_ratio(row: CorporateActionReference) -> tuple[int, int] | None:
    """The exact (a, b) bonus ratio the parser stored, if any."""
    raw = (row.parsed or {}).get("ratio")
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            a, b = int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            return None
        if b != 0:
            return (a, b)
    return None


def _dedupe_scaling(actions: list[CachedCorporateAction]) -> list[CachedCorporateAction]:
    """Auto-applicable scaling events, one per (ex-date, multiplier).

    The feed carries one row per exchange, so the same split/bonus arrives twice
    (NSE + BSE). Replaying both would double-count the adjustment, so collapse to a
    single event per (ex-date, normalised multiplier) before applying.
    """
    seen: set[tuple] = set()
    deduped: list[CachedCorporateAction] = []
    for a in actions:
        if (
            a.parsed_type not in {CorpActionType.BONUS.value, CorpActionType.SPLIT.value}
            or a.needs_review
            or a.unit_multiplier is None
            or a.reference_id is None
        ):
            continue
        key = (a.ex_date, a.unit_multiplier.normalize())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)
    return deduped


def _replay_corporate_actions(
    txns: list,
    security: Security,
    cached_actions: list[CachedCorporateAction],
) -> ReplayMatch | None:
    """Net units after applying the cached scaling events over the real timeline.

    Returns ``None`` when there are no auto-applicable events (detection then has
    nothing to suggest). Mirrors how ``apply_corporate_actions_to_folio`` builds and
    applies events, so the prediction matches what "Apply" would actually do. Applies
    one event at a time, in ex-date order, recording the holding before and after each
    so the UI can preview every step.
    """
    deduped = sorted(_dedupe_scaling(cached_actions), key=lambda a: a.ex_date)
    if not deduped:
        return None
    core_security = to_core_security(security)
    running = list(txns)
    steps: list[ReplayStep] = []
    try:
        for a in deduped:
            ref = f"ca-ref:{a.reference_id}"
            event = CorporateActionApplyEvent(
                kind=CorpActionType(a.parsed_type),
                ex_date=a.ex_date,
                security=core_security,
                unit_multiplier=a.unit_multiplier,
                bonus_ratio=a.bonus_ratio,
                source_ref=ref,
            )
            # Already in the ledger (a prior apply wrote it)? Re-applying is an
            # idempotent no-op, so it must not appear as an outstanding step — else a
            # reconciled holding keeps "suggesting" an action that's already done. A
            # bonus row sits at its ex-date, so its before/after still spans the jump
            # and the units-changed check alone can't tell it's applied.
            already_applied = any(txn.source_ref == ref for txn in running)
            before = held_units_asof(running, core_security, a.ex_date)
            running = apply_corporate_action_events(running, [event])
            after = held_units_asof(running, core_security, a.ex_date + timedelta(days=1))
            if not already_applied:
                steps.append(ReplayStep(action=a, units_before=before, units_after=after))
    except (ValueError, KeyError):
        # A malformed cached event can't be simulated; treat as "no clean replay" so
        # detection falls back to a manual flag rather than crashing reconciliation.
        return None
    return ReplayMatch(replayed_units=net_units_from_transactions(running), steps=steps)


def _annotate_corporate_actions(
    result: ReconciliationResult,
    *,
    security: Security,
    incomplete_history: bool,
    txns: list,
    ledger_txns: list | None = None,
) -> ReconciliationResult:
    """Run the detection ruleset and merge issues into ``result``."""
    issues = strip_corporate_action_issues(result.issues)
    cached_actions = _cached_actions_for_security(security)
    replay = None
    net = result.units_from_transactions
    holding = result.units_from_holdings
    # Replay uses the full folio timeline (including display-only partial rows), not
    # just cost-basis rows — orphan sells live in the latter bucket but still need
    # scaling events applied in date order to test whether a cached CA explains eCAS.
    replay_source = ledger_txns if ledger_txns else txns
    has_gap = net is not None and holding is not None and holding - net > TOLERANCE
    needs_replay_probe = incomplete_history or (net is not None and net < _ZERO) or has_gap
    if replay_source and needs_replay_probe and _dedupe_scaling(cached_actions):
        replay = _replay_corporate_actions(replay_source, security, cached_actions)
    ca_issues = detect_corporate_action_issues(
        net_units=net,
        holding_units=holding,
        incomplete_history=incomplete_history,
        cached_actions=cached_actions,
        replay=replay,
    )
    if ca_issues:
        if ca_issues[0].get("type") == "corporate_action_suggestion":
            issues = [i for i in issues if i.get("type") != "incomplete_history"]
        issues = [*issues, *ca_issues]
    if not issues:
        return result
    return result.model_copy(update={"issues": issues})


def _annotate_opening_lot(
    result: ReconciliationResult,
    *,
    investor: Investor,
    security: Security,
    folio: Folio,
) -> ReconciliationResult:
    """Flag an eCAS-only equity that needs an opening lot, or mark that one is on file.

    The ``opening_lot_recorded`` marker (informational — it does not flag needs-attention)
    lets the UI offer to remove a recorded lot, e.g. after a mis-entry.
    """
    from folioman_core.corporate_action_detect import strip_opening_lot_issues

    if security.security_type != SecurityType.EQUITY.value:
        return result

    base_ref = f"opening-lot:{folio.id}:{security.id}"
    has_opening_lot = investor.transactions.filter(
        folio=folio, security=security, source_ref__startswith=base_ref
    ).exists()
    if has_opening_lot:
        # An opening lot exists (any status) — surface it so it can be removed, and clear
        # any stale prompt. Dedup both opening-lot markers so a re-reconcile is idempotent.
        issues = [
            i
            for i in result.issues
            if i.get("type") not in ("opening_lot_needed", "opening_lot_recorded")
        ]
        issues.append({"type": "opening_lot_recorded"})
        return result.model_copy(update={"issues": issues})

    if result.status is not IntegrityStatus.SNAPSHOT_ONLY:
        return result
    if result.units_from_holdings is None or result.units_from_holdings <= 0:
        return result

    issues = strip_opening_lot_issues(result.issues)
    issues = [
        i
        for i in issues
        if not (i.get("type") == "corporate_action_manual" and i.get("reason") == "snapshot_only")
    ]
    issues.append(
        {
            "type": "opening_lot_needed",
            "holding_units": str(result.units_from_holdings),
            "classifications": [
                "ipo_allotment",
                "transfer_in",
                "demerger_result",
            ],
        }
    )
    return result.model_copy(update={"issues": issues})


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
    # Cost-basis ledger with corporate actions applied in memory (split-scaled units,
    # merged lots, bonus shares) — read from the projection, never from rewritten rows.
    txns = compute_ledger(investor, security, folio=folio)
    holdings_qs = investor.holdings.filter(security=security, folio=folio)
    # A pure eCAS-only zero holding carries no position — e.g. a rights entitlement (a
    # transient line) that lapsed or was exercised, now reported as 0. Drop it so it
    # doesn't surface as a snapshot-only row begging a (0-unit) opening lot. A zero
    # holding alongside transactions is kept (a fully-exited ledger still reconciles
    # net 0 against it).
    if not investor.transactions.filter(security=security, folio=folio).exists():
        holdings_qs = holdings_qs.filter(units__gt=0)
    holdings = [to_core_holding(h) for h in holdings_qs.select_related("security", "folio")]
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

    partial = PartialBlock.objects.filter(investor=investor, security=security, folio=folio).first()
    # Rows kept for display only (a partial bucket has no cost-basis txns above);
    # used to date the ledger, not to state a holding.
    display_txns = [
        to_core_transaction(t) for t in investor.transactions.filter(security=security, folio=folio)
    ]

    result = reconcile(txns or None, holdings or None, user_acknowledged=user_acknowledged)

    if partial is not None and not txns and display_txns:
        if result is None:
            result = ReconciliationResult(
                status=IntegrityStatus.SNAPSHOT_ONLY,
                tax_safe=False,
                # No reliable net for a mid-history ledger (missing prior buys);
                # the incomplete_history issue carries `missing_prior_units`.
                units_from_transactions=None,
                units_from_holdings=None,
                issues=[_incomplete_history_issue(partial)],
            )
        else:
            result = _annotate_incomplete_history(result, partial=partial)

    incomplete_history = partial is not None or (
        result is not None
        and result.units_from_transactions is not None
        and result.units_from_transactions < 0
    )
    if result is not None and security.security_type == SecurityType.EQUITY.value:
        result = _annotate_corporate_actions(
            result,
            security=security,
            incomplete_history=incomplete_history,
            txns=txns,
            ledger_txns=display_txns,
        )
        result = _annotate_opening_lot(
            result,
            investor=investor,
            security=security,
            folio=folio,
        )

    if result is None:
        # Nothing to reconcile in this folio — drop any stale status.
        if existing:
            existing.delete()
        return None

    # Temporal context for the comparison: how far each side's evidence reaches.
    ledger_through = max((t.date for t in txns), default=None)
    if ledger_through is None and display_txns:
        ledger_through = max(t.date for t in display_txns)
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

    if security.security_type == SecurityType.EQUITY.value:
        try:
            attribute_dividends_for_security(investor, security)
        except Exception:
            logger.exception(
                "dividend attribution failed for security %s (investor %s)",
                security.id,
                investor.id,
            )

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
