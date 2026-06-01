"""Corporate-action helpers that emit ledger rows for FIFO and reconciliation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from folioman_core.models.security import Security
from folioman_core.models.transaction import Transaction, TransactionSource, TransactionType

_ZERO = Decimal("0")


# Same-date ordering: acquisitions settle before disposals so FIFO never sees a
# sell ahead of its buy. source_ref is only a final, stable tiebreaker.
_TYPE_ORDER: dict[TransactionType, int] = {
    TransactionType.SPLIT: 0,
    TransactionType.BONUS: 1,
    TransactionType.TRANSFER_IN: 2,
    TransactionType.BUY: 3,
    TransactionType.DIVIDEND: 4,
    TransactionType.SELL: 5,
    TransactionType.TRANSFER_OUT: 6,
}


def _sort_transactions(transactions: list[Transaction]) -> list[Transaction]:
    return sorted(transactions, key=lambda row: (row.date, _TYPE_ORDER[row.type], row.source_ref))


def apply_split(
    transactions: list[Transaction],
    *,
    ratio: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Apply a ``ratio``-for-1 split (``ratio=2`` → 1:2) to history before ``effective_date``."""
    if ratio <= _ZERO:
        msg = "split ratio must be positive"
        raise ValueError(msg)

    adjusted: list[Transaction] = []
    for txn in transactions:
        if txn.date >= effective_date or txn.security != security:
            adjusted.append(txn)
            continue
        if txn.type in (
            TransactionType.BUY,
            TransactionType.SELL,
            TransactionType.BONUS,
            TransactionType.TRANSFER_IN,
            TransactionType.TRANSFER_OUT,
        ):
            adjusted.append(
                txn.model_copy(
                    update={
                        "units": txn.units * ratio,
                        "nav_or_price": txn.nav_or_price / ratio,
                    }
                )
            )
        else:
            adjusted.append(txn)

    marker = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.SPLIT,
        units=_ZERO,
        nav_or_price=_ZERO,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref or f"split:{ratio}",
    )
    adjusted.append(marker)
    return _sort_transactions(adjusted)


def apply_bonus(
    transactions: list[Transaction],
    *,
    bonus_units: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Record bonus shares at zero cost."""
    if bonus_units <= _ZERO:
        msg = "bonus_units must be positive"
        raise ValueError(msg)

    bonus = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.BONUS,
        units=bonus_units,
        nav_or_price=_ZERO,
        amount=_ZERO,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref,
    )
    return _sort_transactions([*transactions, bonus])


def record_dividend(
    *,
    amount: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> Transaction:
    """Create a dividend ledger row (cash only, zero units)."""
    if amount <= _ZERO:
        msg = "dividend amount must be positive"
        raise ValueError(msg)

    return Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.DIVIDEND,
        units=_ZERO,
        nav_or_price=_ZERO,
        amount=amount,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref,
    )
