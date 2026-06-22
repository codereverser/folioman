"""Record a non-trade opening lot for an eCAS-only equity holding."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction as db_transaction
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.fifo import net_units_from_transactions
from folioman_core.models import SecurityType, TransactionSource
from folioman_core.opening_lot import OpeningLotKind, transaction_type_for_opening_lot
from folioman_core.reconciliation import TOLERANCE

from folioman_app.mappers import to_core_transaction
from folioman_app.models import (
    AppliedCorporateAction,
    Folio,
    Investor,
    Security,
    Transaction,
)
from folioman_app.services.demerger_match import find_demerger_parent
from folioman_app.tasks.reconcile import reconcile_security_folio

_ZERO = Decimal("0")
_NARRATION = {
    OpeningLotKind.IPO_ALLOTMENT: "IPO allotment (opening lot)",
    OpeningLotKind.TRANSFER_IN: "Transfer in (opening lot)",
    OpeningLotKind.DEMERGER_RESULT: "Demerger receipt (opening lot)",
}


def _opening_lot_source_ref(folio_id: int, security_id: int) -> str:
    return f"opening-lot:{folio_id}:{security_id}"


def _holding_units(investor: Investor, security: Security, folio: Folio) -> Decimal | None:
    row = (
        investor.holdings.filter(security=security, folio=folio)
        .order_by("-as_of_date")
        .values_list("units", flat=True)
        .first()
    )
    return row


@db_transaction.atomic
def record_opening_lot(
    investor: Investor,
    folio: Folio,
    security: Security,
    *,
    kind: OpeningLotKind,
    lot_date: date,
    units: Decimal | None = None,
    price: Decimal | None = None,
    cost_basis_unknown: bool = False,
) -> dict:
    """Write one opening lot so an eCAS-only equity can reconcile to full history."""
    if security.security_type != SecurityType.EQUITY.value:
        msg = "opening lots apply to equities only"
        raise ValueError(msg)
    if folio.folio_type != "demat":
        msg = "opening lots require a demat folio"
        raise ValueError(msg)

    holding = _holding_units(investor, security, folio)
    if holding is None or holding <= _ZERO:
        msg = "no eCAS holding to open against"
        raise ValueError(msg)

    lot_units = units if units is not None else holding
    if lot_units <= _ZERO:
        msg = "units must be positive"
        raise ValueError(msg)
    if abs(lot_units - holding) > TOLERANCE:
        msg = f"units must match the eCAS holding ({holding})"
        raise ValueError(msg)

    source_ref = _opening_lot_source_ref(folio.id, security.id)
    if Transaction.objects.filter(investor=investor, folio=folio, source_ref=source_ref).exists():
        msg = "opening lot already recorded for this holding"
        raise ValueError(msg)

    txn_type = transaction_type_for_opening_lot(kind)
    if cost_basis_unknown or price is None:
        nav = _ZERO
        amount = _ZERO
        complete = False
    else:
        nav = price
        amount = (lot_units * price).quantize(Decimal("0.01"))
        complete = True

    narration = {
        OpeningLotKind.IPO_ALLOTMENT: "IPO allotment (opening lot)",
        OpeningLotKind.TRANSFER_IN: "Transfer in (opening lot)",
        OpeningLotKind.DEMERGER_RESULT: "Demerger receipt (opening lot)",
    }[kind]

    Transaction.objects.create(
        investor=investor,
        security=security,
        folio=folio,
        date=lot_date,
        transaction_type=txn_type.value,
        units=lot_units,
        nav_or_price=nav,
        amount=amount,
        currency=security.currency or "INR",
        source=TransactionSource.MANUAL.value,
        source_ref=source_ref,
        narration=narration,
        cost_basis_complete=complete,
    )
    reconcile_security_folio(investor, security, folio)
    return {
        "created": 1,
        "classification": kind.value,
        "units": str(lot_units),
        "cost_basis_complete": complete,
    }


def _link_demerger_parent(
    investor: Investor,
    folio: Folio,
    child: Security,
    units_by_date: dict,
    cost_by_date: dict,
    ex_date: date,
) -> Security | None:
    """Fingerprint-match the child's received lots to a parent and record the link.

    Stores one ``AppliedCorporateAction`` (kind ``demerger``) on the parent with the
    per-acquisition-date cost the children carry away and the demerger ``ex_date``, so
    the FIFO pass sheds that cost from the parent lots still open at the ex-date (a
    parent sale *before* the demerger keeps its full basis). Returns the matched parent
    (or ``None`` when no single confident match exists — the caller leaves it for the user).
    """
    match = find_demerger_parent(investor, folio, child, units_by_date)
    if match is None:
        return None
    AppliedCorporateAction.objects.update_or_create(
        investor=investor,
        folio=folio,
        security=match.security,
        source_ref=f"demerger:{child.id}",
        defaults={
            "counterparty_security": child,
            "kind": CorpActionType.DEMERGER.value,
            "ex_date": ex_date,
            "params": {
                "reductions": {d.isoformat(): str(cost) for d, cost in cost_by_date.items()}
            },
        },
    )
    return match.security


@db_transaction.atomic
def record_opening_lots(
    investor: Investor,
    folio: Folio,
    security: Security,
    *,
    kind: OpeningLotKind,
    lots: list[dict],
    cost_basis_unknown: bool = False,
    demerger_date: date | None = None,
) -> dict:
    """Record several opening lots for one no-history equity — the per-lot acquisition
    a demerger receipt carries (the broker allocates a date + cost to each child lot).

    Unlike :func:`record_opening_lot`, this does not require a current eCAS holding: a
    child that was already sold has only orphan sells (flagged cost-basis-incomplete at
    import). Adding the receipt lots (dated to the inherited parent acquisition dates)
    makes FIFO solvent, so the completeness re-derivation flips those sells back to
    complete and they enter capital gains. Each lot is ``{lot_date, units, price}``
    (``price`` optional / omitted when ``cost_basis_unknown``).

    ``demerger_date`` is the demerger's ex-date; with it (and known cost) the receipt is
    matched to its parent and the parent's cost basis is reduced *at that date*, so a
    parent sale before the demerger keeps its full basis. Without it the lots are still
    recorded but the parent is left unlinked (the ex-date can't be guessed safely).
    """
    from folioman_app.tasks.import_csv import _reconcile_cost_basis_completeness

    if security.security_type != SecurityType.EQUITY.value:
        msg = "opening lots apply to equities only"
        raise ValueError(msg)
    if folio.folio_type != "demat":
        msg = "opening lots require a demat folio"
        raise ValueError(msg)
    if not lots:
        msg = "at least one lot is required"
        raise ValueError(msg)

    base_ref = _opening_lot_source_ref(folio.id, security.id)
    if Transaction.objects.filter(
        investor=investor, folio=folio, source_ref__startswith=base_ref
    ).exists():
        msg = "opening lots already recorded for this holding"
        raise ValueError(msg)

    txn_type = transaction_type_for_opening_lot(kind)
    created = 0
    # Aggregate the receipt by acquisition date so a demerger child can be matched to
    # its parent (date fingerprint) and the parent's cost reduced by what left with it.
    units_by_date: dict[date, Decimal] = {}
    cost_by_date: dict[date, Decimal] = {}
    all_priced = not cost_basis_unknown
    for index, lot in enumerate(lots):
        lot_units = lot["units"]
        if lot_units <= _ZERO:
            msg = "lot units must be positive"
            raise ValueError(msg)
        price = lot.get("price")
        if cost_basis_unknown or price is None:
            nav, amount, complete = _ZERO, _ZERO, False
            all_priced = False
        else:
            nav = price
            amount = (lot_units * price).quantize(Decimal("0.01"))
            complete = True
            lot_date = lot["lot_date"]
            units_by_date[lot_date] = units_by_date.get(lot_date, _ZERO) + lot_units
            cost_by_date[lot_date] = cost_by_date.get(lot_date, _ZERO) + lot_units * price
        Transaction.objects.create(
            investor=investor,
            security=security,
            folio=folio,
            date=lot["lot_date"],
            transaction_type=txn_type.value,
            units=lot_units,
            nav_or_price=nav,
            amount=amount,
            currency=security.currency or "INR",
            source=TransactionSource.MANUAL.value,
            source_ref=f"{base_ref}:{index}",
            narration=_NARRATION[kind],
            cost_basis_complete=complete,
        )
        created += 1

    # A demerger receipt inherits the parent's cost basis: match it back to the parent
    # and record the cost the parent shed at the demerger ex-date. Only when every lot is
    # priced (an unknown basis can't reduce anything) and the ex-date is known (it places
    # the reduction so a pre-demerger parent sale keeps its full basis — it can't be
    # guessed). The ex-date must fall on/after the inherited acquisition dates.
    suggested_parent = None
    if kind is OpeningLotKind.DEMERGER_RESULT and all_priced and units_by_date and demerger_date:
        if demerger_date < max(units_by_date):
            msg = "demerger date cannot precede the received lots' acquisition dates"
            raise ValueError(msg)
        parent = _link_demerger_parent(
            investor, folio, security, units_by_date, cost_by_date, demerger_date
        )
        if parent is not None:
            suggested_parent = {
                "id": parent.id,
                "name": parent.name,
                "isin": parent.isin,
            }

    # Receipt lots may make a previously-orphan sell solvent → flip it back to complete.
    _reconcile_cost_basis_completeness(investor, security)
    reconcile_security_folio(investor, security, folio)
    if suggested_parent is not None:
        # The parent's cost basis moved; refresh its reconciliation too.
        parent_security = Security.objects.get(id=suggested_parent["id"])
        reconcile_security_folio(investor, parent_security, folio)
    net = net_units_from_transactions(
        [
            to_core_transaction(t)
            for t in investor.transactions.filter(security=security, folio=folio)
        ]
    )
    return {"created": created, "net_units": str(net), "suggested_parent": suggested_parent}
