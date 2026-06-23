"""Apply corporate-action events to an investor folio ledger."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction as db_transaction
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.corporate_actions import (
    COST_BASIS_RELIABLE_SINCE,
    CorporateActionApplyEvent,
)
from folioman_core.fifo import net_units_from_transactions, open_lots_asof
from folioman_core.models import HoldingSource, SecurityType
from folioman_core.models.security import Security as CoreSecurity
from folioman_core.models.transaction import TransactionSource, TransactionType

from folioman_app.mappers import to_core_security
from folioman_app.models import (
    AppliedCorporateAction,
    CorporateActionReference,
    Folio,
    Investor,
    Security,
    Transaction,
)
from folioman_app.services.projected_ledger import compute_ledger
from folioman_app.tasks._upsert import upsert_security
from folioman_app.tasks.reconcile import reconcile_security

# Sub-share remainders below this are dust, not a fractional entitlement (matches
# reconciliation TOLERANCE).
_FRACTION_EPSILON = Decimal("0.0001")
_ONE = Decimal("1")


def _parsed_ratio(ref: CorporateActionReference) -> tuple[int, int] | None:
    """The exact (a, b) bonus ratio the parser stored, if any — for integer-share
    bonus issuance that a truncated decimal multiplier can't do reliably."""
    raw = (ref.parsed or {}).get("ratio")
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            a, b = int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            return None
        if b != 0:
            return (a, b)
    return None


def _event_from_reference(
    ref: CorporateActionReference, security: Security
) -> CorporateActionApplyEvent:
    # Only bonus/split are auto-applicable from a cached feed reference: they replay
    # as deterministic unit transforms. Everything else is rejected here —
    # merger/demerger need a counterparty and lot semantics the reference can't carry;
    # a DIVIDEND reference would double-write against the dividend-attribution pass
    # (see apply_corporate_action_events); rights/buyback need units/price a feed row
    # doesn't supply. These reach the ledger only through explicit manual authoring.
    if ref.needs_review or ref.parsed_type not in {
        CorpActionType.BONUS.value,
        CorpActionType.SPLIT.value,
    }:
        msg = f"reference {ref.id} ({ref.subject!r}) is not auto-applicable"
        raise ValueError(msg)
    return CorporateActionApplyEvent(
        kind=CorpActionType(ref.parsed_type),
        ex_date=ref.ex_date,
        security=to_core_security(security),
        unit_multiplier=ref.unit_multiplier,
        bonus_ratio=_parsed_ratio(ref),
        dividend_per_share=ref.amount,
        source_ref=f"ca-ref:{ref.id}",
    )


def _persist_applied_events(
    investor: Investor,
    folio: Folio,
    events: Sequence[CorporateActionApplyEvent],
) -> tuple[int, set[int]]:
    """Record each event in the corporate-action log (never rewriting trade rows).

    The as-traded ``Transaction`` rows stay exactly as imported; the read-time
    projection (``compute_ledger``) replays these events to derive the split-scaled /
    merged / bonus view. Returns (events persisted, affected security ids). A merger's
    acquirer is upserted so it exists as a security to reconcile and project onto.
    """
    created = 0
    affected: set[int] = set()
    for event in events:
        if event.kind is CorpActionType.MERGER:
            # The affected security merges away (``security`` on the log row); the
            # acquirer is the counterparty the lots rebase onto.
            old = event.merger_old_security
            acquirer = upsert_security(event.merger_new_security or event.security)
            security = Security.objects.filter(isin=old.isin).first() or upsert_security(old)
            counterparty = acquirer
            affected.add(acquirer.id)
        else:
            security = Security.objects.filter(isin=event.security.isin).first() or upsert_security(
                event.security
            )
            counterparty = None
        affected.add(security.id)
        ratio = event.bonus_ratio
        _, was_created = AppliedCorporateAction.objects.update_or_create(
            investor=investor,
            folio=folio,
            security=security,
            source_ref=event.source_ref,
            defaults={
                "counterparty_security": counterparty,
                "kind": event.kind.value,
                "ex_date": event.ex_date,
                "unit_multiplier": event.unit_multiplier,
                "bonus_ratio_a": ratio[0] if ratio else None,
                "bonus_ratio_b": ratio[1] if ratio else None,
                "merger_ratio": event.merger_ratio,
                "units": event.rights_units,
                "price": event.rights_price,
                "dividend_per_share": event.dividend_per_share,
            },
        )
        created += 1 if was_created else 0
    return created, affected


def _flag_pre2016_cost_basis(investor: Investor, folio: Folio, security_ids: set[int]) -> None:
    """Mark pre-window equity acquisitions as cost-basis-incomplete (display only).

    Tradebook / corporate-action feeds are reliable for cost basis only from
    ``COST_BASIS_RELIABLE_SINCE``; an earlier acquisition's cost is untrustworthy, so it
    is kept for units but excluded from cost basis (the projection only feeds complete
    rows to FIFO). This is a reliability annotation, not a trade rewrite — units and
    price are untouched. Applied to the securities a corporate action just touched.
    """
    Transaction.objects.filter(
        investor=investor,
        folio=folio,
        security_id__in=security_ids,
        security__security_type=SecurityType.EQUITY.value,
        transaction_type__in=[
            TransactionType.BUY.value,
            TransactionType.BONUS.value,
            TransactionType.TRANSFER_IN.value,
        ],
        date__lt=COST_BASIS_RELIABLE_SINCE,
        cost_basis_complete=True,
    ).update(cost_basis_complete=False)


def _filter_unapplied_events(
    investor: Investor,
    folio: Folio,
    events: list[CorporateActionApplyEvent],
) -> list[CorporateActionApplyEvent]:
    """Drop events whose ``source_ref`` is already recorded in the event log."""
    existing = set(
        AppliedCorporateAction.objects.filter(investor=investor, folio=folio)
        .exclude(source_ref="")
        .values_list("source_ref", flat=True)
    )
    out: list[CorporateActionApplyEvent] = []
    for event in events:
        if event.source_ref and event.source_ref in existing:
            continue
        out.append(event)
    return out


def _settle_fractional_entitlement(investor: Investor, folio: Folio, security: Security) -> bool:
    """Book a cost-priced cash disposal for a sub-share corporate-action remainder.

    An Indian demat holds whole shares; a merger / odd-bonus ratio can leave the
    ledger net a fraction above the whole eCAS holding (the registrar sells the
    fraction for cash). When the eCAS anchor is a whole number and the ledger is over
    it by **< 1 share**, book that fraction as a SELL at the oldest open lot's cost —
    zero realised gain — so the ledger reconciles to whole shares. A larger gap is
    genuine missing history and is left to surface as a MISMATCH.

    The row is tagged (``source=CORPORATE_ACTION`` + a stable ``source_ref``) and the
    pass is idempotent, so it never double-books and a future manual override can
    find or replace it. Returns True if a settlement row was booked.

    Self-scoping to whole-share markets: only equity in INR is considered, and a
    foreign (fractional-share) holding has no eCAS anchor to settle against anyway.
    """
    if security.security_type != SecurityType.EQUITY.value or (security.currency or "INR") != "INR":
        return False

    holding = (
        investor.holdings.filter(security=security, folio=folio, source=HoldingSource.ECAS.value)
        .order_by("-as_of_date")
        .first()
    )
    if holding is None or holding.units != holding.units.to_integral_value():
        return False  # no anchor, or the anchor itself isn't a whole share count

    core_txns = compute_ledger(investor, security, folio=folio)
    if not core_txns:
        return False
    excess = net_units_from_transactions(core_txns) - holding.units
    if excess <= _FRACTION_EPSILON or excess >= _ONE:
        return False  # dust, exact, or a real (>= 1 share) gap

    source_ref = f"fractional-entitlement:{security.isin or security.symbol}"
    if investor.transactions.filter(folio=folio, security=security, source_ref=source_ref).exists():
        return False  # already settled (or a prior manual override stands)

    # Sell the fraction at the oldest open lot's cost so the realised gain is zero.
    sell_date = max(t.date for t in core_txns)
    lots = open_lots_asof(
        core_txns,
        to_core_security(security),
        sell_date + timedelta(days=1),
        folio_number=folio.number,
    )
    if not lots:
        return False
    Transaction.objects.create(
        investor=investor,
        security=security,
        folio=folio,
        date=sell_date,
        transaction_type=TransactionType.SELL.value,
        units=excess,
        nav_or_price=lots[0].cost_per_unit,
        source=TransactionSource.CORPORATE_ACTION.value,
        source_ref=source_ref,
        cost_basis_complete=True,
    )
    return True


@db_transaction.atomic
def apply_corporate_actions_to_folio(
    investor: Investor,
    folio: Folio,
    *,
    reference_ids: Sequence[int] = (),
    events: Sequence[CorporateActionApplyEvent] = (),
) -> dict:
    """Apply cached references and/or explicit events; reconcile affected securities."""
    all_events: list[CorporateActionApplyEvent] = list(events)
    for ref_id in reference_ids:
        ref = CorporateActionReference.objects.select_related("security").get(pk=ref_id)
        security = ref.security or Security.objects.filter(isin=ref.isin).first()
        if security is None:
            msg = f"no security for corporate-action reference {ref_id}"
            raise ValueError(msg)
        all_events.append(_event_from_reference(ref, security))

    if not all_events:
        msg = "no corporate actions to apply"
        raise ValueError(msg)

    all_events = _filter_unapplied_events(investor, folio, all_events)
    if not all_events:
        return {
            "updated": 0,
            "created": 0,
            "events_applied": 0,
            "security_ids": [],
            "skipped": "already_applied",
        }

    # Record the events; the trade rows stay as imported and the projection applies
    # them on read. Flag any pre-window acquisitions the events touch as cost-basis
    # incomplete before anything reads the projection.
    events_written, affected_ids = _persist_applied_events(investor, folio, all_events)

    # A split / bonus can square off an over-sold tradebook (sold against a pre-split
    # balance), so re-derive FIFO completeness now the event is on record — it reads the
    # corporate-action-adjusted ledger and may flip orphan rows back to complete. Do this
    # BEFORE the pre-window reliability flag, which then re-marks pre-2016 acquisitions so
    # the solvency pass doesn't clobber it. Local import: import_csv loads this module.
    from folioman_app.tasks.import_csv import _reconcile_cost_basis_completeness

    affected = list(Security.objects.filter(id__in=affected_ids))
    for sec in affected:
        _reconcile_cost_basis_completeness(investor, sec)
    _flag_pre2016_cost_basis(investor, folio, affected_ids)

    for sec in affected:
        # Whole the ledger against the eCAS anchor before reconciling, so a CA
        # fractional remainder doesn't read as a mismatch.
        _settle_fractional_entitlement(investor, folio, sec)
        reconcile_security(investor, sec)

    return {
        "updated": 0,
        "created": events_written,
        "events_applied": len(all_events),
        "security_ids": sorted(affected_ids),
    }


def _counterparty_security(isin: str, symbol: str, name: str) -> CoreSecurity:
    """The acquiring security in a merger, typed by hand.

    A real ISIN is required so it persists as a distinct security and reconciles;
    name falls back to symbol/ISIN until the resolver fills it.
    """
    isin = (isin or "").strip().upper()
    if not isin:
        msg = "merger requires the counterparty security's ISIN"
        raise ValueError(msg)
    return CoreSecurity(
        type=SecurityType.EQUITY,
        isin=isin,
        symbol=(symbol or "").strip().upper(),
        name=(name or symbol or isin).strip(),
    )


def build_manual_event(
    security: Security,
    *,
    kind: str,
    ex_date: date,
    unit_multiplier: Decimal | None = None,
    merger_ratio: Decimal | None = None,
    units: Decimal | None = None,
    price: Decimal | None = None,
    counterparty_isin: str = "",
    counterparty_symbol: str = "",
    counterparty_name: str = "",
) -> CorporateActionApplyEvent:
    """Build a user-authored corporate-action event for the affected ``security``.

    The path's security is the affected stock; ``kind`` selects the transform and
    the matching params. A merger takes a typed counterparty (acquirer) ISIN.
    Per-kind required fields are validated again by the apply engine, but raise
    here with a clear message first.
    """
    try:
        action = CorpActionType(kind)
    except ValueError as exc:
        msg = f"unknown corporate action kind: {kind!r}"
        raise ValueError(msg) from exc

    core = to_core_security(security)
    ref = f"manual:{action.value}:{ex_date.isoformat()}:{security.isin or security.symbol}"

    if action in (CorpActionType.BONUS, CorpActionType.SPLIT):
        if unit_multiplier is None:
            msg = "bonus/split requires unit_multiplier"
            raise ValueError(msg)
        return CorporateActionApplyEvent(
            kind=action,
            ex_date=ex_date,
            security=core,
            unit_multiplier=unit_multiplier,
            source_ref=ref,
        )
    if action is CorpActionType.MERGER:
        acquirer = _counterparty_security(counterparty_isin, counterparty_symbol, counterparty_name)
        return CorporateActionApplyEvent(
            kind=action,
            ex_date=ex_date,
            security=acquirer,
            merger_old_security=core,
            merger_new_security=acquirer,
            merger_ratio=merger_ratio,
            source_ref=ref,
        )
    if action in (CorpActionType.RIGHTS, CorpActionType.BUYBACK):
        return CorporateActionApplyEvent(
            kind=action,
            ex_date=ex_date,
            security=core,
            rights_units=units,
            rights_price=price,
            source_ref=ref,
        )
    msg = f"corporate action {action.value} is not supported for manual authoring"
    raise ValueError(msg)


def apply_manual_corporate_action(
    investor: Investor,
    folio: Folio,
    security: Security,
    **fields,
) -> dict:
    """Author and apply one corporate action by hand, then re-reconcile."""
    event = build_manual_event(security, **fields)
    return apply_corporate_actions_to_folio(investor, folio, events=[event])


def apply_suggested_corporate_actions(
    investor: Investor,
    folio: Folio,
    security: Security,
    reference_ids: Sequence[int],
) -> dict:
    """Apply the high-confidence cached events from an integrity suggestion.

    A suggestion may span several events (e.g. two splits, or a bonus plus a split),
    all applied together in date order to close one unit gap.
    """
    if not reference_ids:
        msg = "no corporate-action references to apply"
        raise ValueError(msg)
    refs = {r.id: r for r in CorporateActionReference.objects.filter(pk__in=reference_ids)}
    for ref_id in reference_ids:
        ref = refs.get(ref_id)
        if ref is None:
            msg = f"unknown corporate-action reference {ref_id}"
            raise ValueError(msg)
        # Trust the security FK when present: an event can be cached under a prior ISIN
        # (a face-value split changes the ISIN, so the pre-split row carries the old
        # one) yet still be linked to this security. Only fall back to the ISIN check
        # when there's no FK to rely on.
        if ref.security_id is not None:
            if ref.security_id != security.id:
                msg = "reference does not match this security"
                raise ValueError(msg)
        elif ref.isin and ref.isin != security.isin:
            msg = "reference does not match this security"
            raise ValueError(msg)
    return apply_corporate_actions_to_folio(investor, folio, reference_ids=list(reference_ids))
