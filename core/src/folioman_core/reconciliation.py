"""Transaction ↔ holding integrity checks (framework-free)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import Field

from folioman_core.fifo import net_units_from_transactions
from folioman_core.models.base import DomainModel
from folioman_core.models.decimal_fields import OptionalDecimalField
from folioman_core.models.holding import Holding
from folioman_core.models.transaction import Transaction

TOLERANCE = Decimal("0.0001")
_ZERO = Decimal("0")


class IntegrityStatus(StrEnum):
    FULL_HISTORY = "full_history"
    SNAPSHOT_ONLY = "snapshot_only"
    RECONCILED = "reconciled"
    MISMATCH = "mismatch"
    USER_ACKNOWLEDGED = "user_acknowledged"


class ReconciliationResult(DomainModel):
    status: IntegrityStatus
    tax_safe: bool
    units_from_transactions: OptionalDecimalField = None
    units_from_holdings: OptionalDecimalField = None
    issues: list[dict[str, Any]] = Field(default_factory=list)


def latest_holding_units(holdings: Sequence[Holding]) -> Decimal | None:
    """Sum units across all holdings on the most recent snapshot date.

    A security held in several demat accounts produces one row per account on
    the same eCAS date; reconciliation compares the *total* against the ledger.
    Assumes transactions are already split-adjusted (raw SPLIT rows contribute
    no units in ``net_units_from_transactions``).
    """
    if not holdings:
        return None
    latest = max(row.as_of_date for row in holdings)
    return sum((row.units for row in holdings if row.as_of_date == latest), _ZERO)


def reconcile_units(
    units_from_transactions: Decimal | None,
    units_from_holdings: Decimal | None,
    *,
    user_acknowledged: bool = False,
) -> ReconciliationResult | None:
    """Pure reconciliation on pre-aggregated unit counts."""
    if units_from_transactions is None and units_from_holdings is None:
        return None

    if units_from_transactions is None and units_from_holdings is not None:
        status = IntegrityStatus.SNAPSHOT_ONLY
        tax_safe = False
        issues: list[dict[str, Any]] = [{"type": "snapshot_only_no_transaction_history"}]
    elif units_from_transactions is not None and units_from_holdings is None:
        status = IntegrityStatus.FULL_HISTORY
        tax_safe = True
        issues = []
    elif abs(units_from_transactions - units_from_holdings) < TOLERANCE:
        status = IntegrityStatus.RECONCILED
        tax_safe = True
        issues = []
    else:
        status = IntegrityStatus.MISMATCH
        tax_safe = False
        issues = [
            {
                "type": "unit_mismatch",
                "tx_units": str(units_from_transactions),
                "holding_units": str(units_from_holdings),
                "delta": str(units_from_holdings - units_from_transactions),
            }
        ]

    if user_acknowledged and status is IntegrityStatus.MISMATCH:
        status = IntegrityStatus.USER_ACKNOWLEDGED
        tax_safe = False
        issues = [{"type": "user_acknowledged", "previous": "mismatch"}]

    return ReconciliationResult(
        status=status,
        tax_safe=tax_safe,
        units_from_transactions=units_from_transactions,
        units_from_holdings=units_from_holdings,
        issues=issues,
    )


def reconcile(
    transactions: Sequence[Transaction] | None,
    holdings: Sequence[Holding] | None,
    *,
    user_acknowledged: bool = False,
) -> ReconciliationResult | None:
    """Reconcile ledger transactions against holding snapshots."""
    tx_units = None if transactions is None else net_units_from_transactions(transactions)
    holding_units = None if holdings is None else latest_holding_units(holdings)
    return reconcile_units(
        tx_units,
        holding_units,
        user_acknowledged=user_acknowledged,
    )
