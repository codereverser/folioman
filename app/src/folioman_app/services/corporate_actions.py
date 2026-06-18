"""Apply corporate-action events to an investor folio ledger."""

from __future__ import annotations

from collections.abc import Sequence

from django.db import transaction as db_transaction
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.corporate_actions import (
    CorporateActionApplyEvent,
    apply_corporate_action_events,
    cost_basis_complete_for_acquisition,
)
from folioman_core.models.transaction import TransactionType

from folioman_app.mappers import to_core_security, to_core_transaction
from folioman_app.models import CorporateActionReference, Folio, Investor, Security, Transaction
from folioman_app.tasks._upsert import upsert_security
from folioman_app.tasks.reconcile import reconcile_security


def _event_from_reference(
    ref: CorporateActionReference, security: Security
) -> CorporateActionApplyEvent:
    if ref.needs_review or ref.parsed_type in {
        CorpActionType.MERGER.value,
        CorpActionType.DEMERGER.value,
        CorpActionType.UNKNOWN.value,
    }:
        msg = f"reference {ref.id} ({ref.subject!r}) is not auto-applicable"
        raise ValueError(msg)
    return CorporateActionApplyEvent(
        kind=CorpActionType(ref.parsed_type),
        ex_date=ref.ex_date,
        security=to_core_security(security),
        unit_multiplier=ref.unit_multiplier,
        dividend_per_share=ref.amount,
        source_ref=f"ca-ref:{ref.id}",
    )


def _securities_for_events(events: Sequence[CorporateActionApplyEvent]) -> set[str]:
    isins: set[str] = set()
    for event in events:
        for sec in (
            event.security,
            event.merger_old_security,
            event.merger_new_security,
            event.demerger_child_security,
        ):
            if sec is not None and sec.isin:
                isins.add(sec.isin)
    return isins


def _cost_basis_complete_for_core(core) -> bool:
    """Mark acquisitions before the reliable window as display-only for cost basis."""
    if core.type in (
        TransactionType.BUY,
        TransactionType.BONUS,
        TransactionType.TRANSFER_IN,
    ):
        return cost_basis_complete_for_acquisition(core.date)
    return True


def _persist_applied_ledger(
    investor: Investor,
    folio: Folio,
    result: list,
) -> tuple[int, int]:
    """Write core apply output back to ORM rows. Returns (updated, created)."""
    from folioman_core.corporate_actions import _stable_source_ref

    updated = 0
    created = 0
    for core in result:
        if core.ledger_id:
            orm = Transaction.objects.filter(
                pk=core.ledger_id, investor=investor, folio=folio
            ).first()
            if orm is None:
                continue
            sec = upsert_security(core.security)
            fields: list[str] = []
            if orm.security_id != sec.id:
                orm.security = sec
                fields.append("security")
            if orm.units != core.units:
                orm.units = core.units
                fields.append("units")
            if orm.nav_or_price != core.nav_or_price:
                orm.nav_or_price = core.nav_or_price
                fields.append("nav_or_price")
            if orm.cost_total != core.cost_total:
                # Exact lot cost preserved by the CA; per-unit above is display-only.
                orm.cost_total = core.cost_total
                fields.append("cost_total")
            if orm.amount != core.amount:
                orm.amount = core.amount
                fields.append("amount")
            complete = _cost_basis_complete_for_core(core)
            if orm.cost_basis_complete != complete:
                orm.cost_basis_complete = complete
                fields.append("cost_basis_complete")
            if fields:
                orm.save(update_fields=[*fields, "updated_at"])
                updated += 1
            continue

        source_ref = core.source_ref or _stable_source_ref(core)
        if Transaction.objects.filter(
            investor=investor, folio=folio, source_ref=source_ref
        ).exists():
            continue

        sec = upsert_security(core.security)
        Transaction.objects.create(
            investor=investor,
            security=sec,
            folio=folio,
            date=core.date,
            transaction_type=core.type.value,
            units=core.units,
            nav_or_price=core.nav_or_price,
            amount=core.amount,
            currency=core.currency,
            fees=core.fees,
            stamp_duty=core.stamp_duty,
            brokerage=core.brokerage,
            cost_total=core.cost_total,
            source=core.source.value,
            source_ref=source_ref,
            cost_basis_complete=_cost_basis_complete_for_core(core),
        )
        created += 1
    return updated, created


def _filter_unapplied_events(
    investor: Investor,
    folio: Folio,
    events: list[CorporateActionApplyEvent],
) -> list[CorporateActionApplyEvent]:
    """Drop events whose ``source_ref`` (or ``ca-ref:{id}``) is already on the ledger."""
    existing = set(
        investor.transactions.filter(folio=folio)
        .exclude(source_ref="")
        .values_list("source_ref", flat=True)
    )
    out: list[CorporateActionApplyEvent] = []
    for event in events:
        ref = event.source_ref
        if not ref and event.kind in {CorpActionType.BONUS, CorpActionType.SPLIT}:
            # Stable key mirrors _stable_source_ref for pre-persist idempotency checks.
            ident = event.security.isin or event.security.symbol or event.security.name
            ref = f"ca:{event.kind.value}:{event.ex_date}:{ident}"
        if ref and ref in existing:
            continue
        out.append(event)
    return out


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

    isins = _securities_for_events(all_events)
    orm_txns = list(
        investor.transactions.filter(folio=folio, security__isin__in=isins).select_related(
            "security", "folio"
        )
    )
    cores = [to_core_transaction(t) for t in orm_txns]
    result = apply_corporate_action_events(cores, all_events)
    updated, created = _persist_applied_ledger(investor, folio, result)

    affected_ids: set[int] = set()
    for isin in isins:
        sec = Security.objects.filter(isin=isin).first()
        if sec is not None:
            reconcile_security(investor, sec)
            affected_ids.add(sec.id)

    return {
        "updated": updated,
        "created": created,
        "events_applied": len(all_events),
        "security_ids": sorted(affected_ids),
    }


def apply_suggested_corporate_action(
    investor: Investor,
    folio: Folio,
    security: Security,
    reference_id: int,
) -> dict:
    """Apply one high-confidence cached event (from an integrity suggestion)."""
    ref = CorporateActionReference.objects.filter(pk=reference_id).first()
    if ref is None:
        msg = f"unknown corporate-action reference {reference_id}"
        raise ValueError(msg)
    if ref.isin and ref.isin != security.isin:
        msg = "reference does not match this security"
        raise ValueError(msg)
    return apply_corporate_actions_to_folio(investor, folio, reference_ids=[reference_id])
