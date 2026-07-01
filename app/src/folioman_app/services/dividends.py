"""Equity dividend schedule display and ledger attribution."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction as db_transaction
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.dividend_attribution import (
    DividendScheduleRow,
    attribution_to_transaction,
)
from folioman_core.dividend_attribution import (
    attribute_dividends_for_folio as compute_dividend_attributions,
)
from folioman_core.models import SecurityType, TransactionSource, TransactionType
from folioman_core.reconciliation import IntegrityStatus

from folioman_app.mappers import to_core_security
from folioman_app.models import CorporateActionReference, Folio, Investor, Security, Transaction
from folioman_app.services.projected_ledger import compute_ledger

_ZERO = Decimal("0")


def _dividend_refs(security: Security) -> list[DividendScheduleRow]:
    filt = CorporateActionReference.objects.filter(parsed_type=CorpActionType.DIVIDEND.value)
    if security.isin:
        filt = filt.filter(isin=security.isin)
    elif security.symbol:
        filt = filt.filter(symbol=security.symbol.upper())
    else:
        return []
    rows: list[DividendScheduleRow] = []
    for ref in filt.filter(needs_review=False).exclude(amount__isnull=True).order_by("-ex_date"):
        rows.append(
            DividendScheduleRow(
                reference_id=ref.id,
                ex_date=ref.ex_date,
                record_date=ref.record_date,
                dividend_per_share=ref.amount,
                subject=ref.subject,
            )
        )
    return rows


def _folio_has_ledger(investor: Investor, security: Security, folio: Folio) -> bool:
    return investor.transactions.filter(security=security, folio=folio).exists()


def _folio_snapshot_only(investor: Investor, security: Security, folio: Folio) -> bool:
    from folioman_app.models import SecurityIntegrityStatus

    status = (
        SecurityIntegrityStatus.objects.filter(investor=investor, security=security, folio=folio)
        .values_list("status", flat=True)
        .first()
    )
    return status == IntegrityStatus.SNAPSHOT_ONLY.value


@db_transaction.atomic
def attribute_dividends_for_folio(investor: Investor, folio: Folio, security: Security) -> int:
    """Reconcile attributed ``DIVIDEND`` rows for one equity folio to the ledger.

    Upserts rather than only creating: a corporate action that changes the units
    held on an ex-date (e.g. a bonus that lands after the shares) must re-price the
    already-attributed dividends, and drop any whose entitlement fell to zero.
    Returns the number of rows created, re-priced, or removed.
    """
    if security.security_type != SecurityType.EQUITY.value:
        return 0
    if not _folio_has_ledger(investor, security, folio):
        return 0
    if _folio_snapshot_only(investor, security, folio):
        return 0

    schedule = _dividend_refs(security)
    if not schedule:
        return 0

    # Units held at each ex-date come from the corporate-action-adjusted projection,
    # so a dividend is attributed on the split/bonus-adjusted share count, not the
    # as-traded one. Recompute the full set (no skip) to correct existing rows.
    cores = compute_ledger(investor, security, folio=folio)
    core_security = to_core_security(security)
    folio_number = folio.number or ""
    desired = {
        row.source_ref: attribution_to_transaction(row, security=core_security)
        for row in compute_dividend_attributions(
            cores,
            security=core_security,
            folio_number=folio_number,
            schedule=schedule,
            existing_source_refs=set(),
        )
    }
    existing = {
        txn.source_ref: txn
        for txn in investor.transactions.filter(
            security=security,
            folio=folio,
            transaction_type=TransactionType.DIVIDEND.value,
            source=TransactionSource.CORPORATE_ACTION.value,
            source_ref__startswith="dividend:ca-ref:",
        )
    }

    changed = 0
    for ref, core in desired.items():
        txn = existing.get(ref)
        if txn is None:
            Transaction.objects.create(
                investor=investor,
                security=security,
                folio=folio,
                date=core.date,
                transaction_type=TransactionType.DIVIDEND.value,
                units=core.units,
                nav_or_price=core.nav_or_price,
                amount=core.amount,
                currency=core.currency,
                source=TransactionSource.CORPORATE_ACTION.value,
                source_ref=ref,
                cost_basis_complete=True,
            )
            changed += 1
        elif txn.amount != core.amount or txn.date != core.date:
            txn.amount = core.amount
            txn.date = core.date
            txn.save(update_fields=["amount", "date"])
            changed += 1

    # Entitlement dropped to zero (e.g. a reverse split / buyback re-based history) —
    # the stale attributed row no longer matches any schedule entitlement.
    stale = set(existing) - set(desired)
    if stale:
        investor.transactions.filter(
            security=security,
            folio=folio,
            transaction_type=TransactionType.DIVIDEND.value,
            source=TransactionSource.CORPORATE_ACTION.value,
            source_ref__in=stale,
        ).delete()
        changed += len(stale)

    return changed


def attribute_dividends_for_security(investor: Investor, security: Security) -> int:
    """Attribute dividends on every ledger folio holding this equity."""
    if security.security_type != SecurityType.EQUITY.value:
        return 0
    folio_ids = set(
        investor.transactions.filter(security=security).values_list("folio_id", flat=True)
    )
    folio_ids.discard(None)
    total = 0
    for folio in Folio.objects.filter(id__in=folio_ids):
        total += attribute_dividends_for_folio(investor, folio, security)
    return total


def build_equity_dividend_detail(
    investor: Investor,
    security: Security,
    *,
    as_of: date,
    current_units: Decimal,
    invested_inr: Decimal | None,
) -> dict:
    """Dividend schedule + received totals for the scheme-detail page."""
    empty = {
        "dividends": [],
        "dividends_received_inr": None,
        "dividend_yield_on_cost": None,
    }
    if security.security_type != SecurityType.EQUITY.value:
        return empty

    schedule = _dividend_refs(security)
    if not schedule:
        return empty

    ledger_orm_txns = list(
        investor.transactions.filter(security=security).select_related("folio", "security")
    )
    has_ledger = bool(ledger_orm_txns)
    attributed_amounts: dict[int, Decimal] = {}
    for txn in ledger_orm_txns:
        if txn.transaction_type != TransactionType.DIVIDEND.value:
            continue
        ref = txn.source_ref or ""
        if not ref.startswith("dividend:ca-ref:"):
            continue
        try:
            ref_id = int(ref.removeprefix("dividend:ca-ref:"))
        except ValueError:
            continue
        attributed_amounts[ref_id] = attributed_amounts.get(ref_id, _ZERO) + (txn.amount or _ZERO)

    from folioman_core.corporate_actions import held_units_asof

    core_security = to_core_security(security)
    # Held-units at each ex-date from the corporate-action-adjusted projection.
    ledger_cores = compute_ledger(investor, security)
    folio_ids = {t.folio_id for t in ledger_orm_txns if t.folio_id}
    folios_by_id = {f.id: f for f in Folio.objects.filter(id__in=folio_ids)}

    rows: list[dict] = []
    for event in schedule:
        row: dict = {
            "reference_id": event.reference_id,
            "ex_date": event.ex_date,
            "record_date": event.record_date,
            "dividend_per_share": event.dividend_per_share,
            "units": None,
            "amount_inr": None,
            "kind": "schedule",
        }
        if has_ledger and event.ex_date <= as_of:
            units = _ZERO
            for folio_id in folio_ids:
                folio = folios_by_id.get(folio_id)
                if folio is None:
                    continue
                held = held_units_asof(
                    ledger_cores,
                    core_security,
                    event.ex_date,
                    folio_number=folio.number or "",
                )
                units += held
            if units > _ZERO:
                row["units"] = units
                if event.reference_id in attributed_amounts:
                    row["amount_inr"] = attributed_amounts[event.reference_id]
                    row["kind"] = "attributed"
                else:
                    row["amount_inr"] = units * event.dividend_per_share
                    row["kind"] = "computed"
        elif not has_ledger:
            if event.ex_date > as_of and current_units > _ZERO:
                row["units"] = current_units
                row["amount_inr"] = current_units * event.dividend_per_share
                row["kind"] = "estimate"
        rows.append(row)

    received = sum(
        (row["amount_inr"] or _ZERO for row in rows if row["kind"] in ("attributed", "computed")),
        _ZERO,
    )
    received_out = received if has_ledger and received > _ZERO else None
    yield_on_cost = None
    if received_out is not None and invested_inr not in (None, _ZERO):
        yield_on_cost = float(received_out / invested_inr)

    return {
        "dividends": rows,
        "dividends_received_inr": received_out,
        "dividend_yield_on_cost": yield_on_cost,
    }
