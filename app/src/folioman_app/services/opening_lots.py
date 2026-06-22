"""Record a non-trade opening lot for an eCAS-only equity holding."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction as db_transaction
from folioman_core.fifo import net_units_from_transactions
from folioman_core.models import SecurityType, TransactionSource
from folioman_core.opening_lot import OpeningLotKind, transaction_type_for_opening_lot
from folioman_core.reconciliation import TOLERANCE

from folioman_app.mappers import to_core_transaction
from folioman_app.models import Folio, Investor, Security, Transaction
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


@db_transaction.atomic
def record_opening_lots(
    investor: Investor,
    folio: Folio,
    security: Security,
    *,
    kind: OpeningLotKind,
    lots: list[dict],
    cost_basis_unknown: bool = False,
) -> dict:
    """Record several opening lots for one no-history equity — the per-lot acquisition
    a demerger receipt carries (the broker allocates a date + cost to each child lot).

    Unlike :func:`record_opening_lot`, this does not require a current eCAS holding: a
    child that was already sold has only orphan sells (flagged cost-basis-incomplete at
    import). Adding the receipt lots (dated to the inherited parent acquisition dates)
    makes FIFO solvent, so the completeness re-derivation flips those sells back to
    complete and they enter capital gains. Each lot is ``{lot_date, units, price}``
    (``price`` optional / omitted when ``cost_basis_unknown``).
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
    for index, lot in enumerate(lots):
        lot_units = lot["units"]
        if lot_units <= _ZERO:
            msg = "lot units must be positive"
            raise ValueError(msg)
        price = lot.get("price")
        if cost_basis_unknown or price is None:
            nav, amount, complete = _ZERO, _ZERO, False
        else:
            nav = price
            amount = (lot_units * price).quantize(Decimal("0.01"))
            complete = True
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

    # Receipt lots may make a previously-orphan sell solvent → flip it back to complete.
    _reconcile_cost_basis_completeness(investor, security)
    reconcile_security_folio(investor, security, folio)
    net = net_units_from_transactions(
        [
            to_core_transaction(t)
            for t in investor.transactions.filter(security=security, folio=folio)
        ]
    )
    return {"created": created, "net_units": str(net)}
