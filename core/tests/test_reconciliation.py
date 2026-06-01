"""Reconciliation integrity."""

from datetime import date
from decimal import Decimal

import pytest
from folioman_core.models import (
    Holding,
    HoldingSource,
    Security,
    SecurityType,
    Transaction,
    TransactionSource,
    TransactionType,
)
from folioman_core.reconciliation import (
    TOLERANCE,
    IntegrityStatus,
    ReconciliationResult,
    reconcile,
    reconcile_units,
)

_MF = Security(type=SecurityType.MF, name="Sample MF", amfi_code="122639")
_EQUITY = Security(
    type=SecurityType.EQUITY,
    name="Reliance",
    isin="INE002A01018",
    symbol="RELIANCE",
)


def _buy(units: str) -> Transaction:
    return Transaction(
        security=_MF,
        date=date(2024, 1, 1),
        type=TransactionType.BUY,
        units=units,
        nav_or_price="10",
        amount=str(Decimal(units) * 10),
        source=TransactionSource.CAS_PDF,
    )


def _holding(units: str, *, on: date = date(2025, 3, 15)) -> Holding:
    return Holding(
        security=_EQUITY,
        as_of_date=on,
        units=units,
        source=HoldingSource.ECAS,
    )


@pytest.mark.parametrize(
    ("tx_units", "holding_units", "expected_status", "tax_safe"),
    [
        (None, None, None, None),
        (None, Decimal("50"), IntegrityStatus.SNAPSHOT_ONLY, False),
        (Decimal("125.4320"), None, IntegrityStatus.FULL_HISTORY, True),
        (Decimal("100"), Decimal("100"), IntegrityStatus.RECONCILED, True),
        (Decimal("100"), Decimal("110"), IntegrityStatus.MISMATCH, False),
    ],
)
def test_reconcile_units_five_cases(
    tx_units: Decimal | None,
    holding_units: Decimal | None,
    expected_status: IntegrityStatus | None,
    tax_safe: bool | None,
):
    result = reconcile_units(tx_units, holding_units)
    if expected_status is None:
        assert result is None
        return
    assert isinstance(result, ReconciliationResult)
    assert result.status is expected_status
    assert result.tax_safe is tax_safe


def test_reconcile_units_within_tolerance():
    result = reconcile_units(Decimal("100.00005"), Decimal("100.00000"))
    assert result is not None
    assert result.status is IntegrityStatus.RECONCILED


def test_reconcile_units_mismatch_delta_issue():
    result = reconcile_units(Decimal("50"), Decimal("60"))
    assert result is not None
    assert result.status is IntegrityStatus.MISMATCH
    assert result.issues[0]["delta"] == str(Decimal("10"))


def test_user_acknowledged_overrides_mismatch():
    result = reconcile_units(
        Decimal("50"),
        Decimal("60"),
        user_acknowledged=True,
    )
    assert result is not None
    assert result.status is IntegrityStatus.USER_ACKNOWLEDGED
    assert result.tax_safe is False


def test_reconcile_from_ledger_full_history():
    result = reconcile(transactions=[_buy("100")], holdings=None)
    assert result is not None
    assert result.status is IntegrityStatus.FULL_HISTORY
    assert result.units_from_transactions == Decimal("100")


def test_reconcile_from_ledger_snapshot_only():
    result = reconcile(transactions=None, holdings=[_holding("10")])
    assert result is not None
    assert result.status is IntegrityStatus.SNAPSHOT_ONLY


def test_reconcile_from_ledger_reconciled():
    result = reconcile(
        transactions=[_buy("10")],
        holdings=[
            Holding(
                security=_MF,
                as_of_date=date(2025, 1, 1),
                units="10",
                source=HoldingSource.MANUAL,
            )
        ],
    )
    assert result is not None
    assert result.status is IntegrityStatus.RECONCILED


def test_reconcile_sums_multi_account_holdings():
    h1 = Holding(
        security=_EQUITY,
        as_of_date=date(2025, 3, 15),
        units="50",
        source=HoldingSource.ECAS,
        broker="A",
    )
    h2 = Holding(
        security=_EQUITY,
        as_of_date=date(2025, 3, 15),
        units="60",
        source=HoldingSource.ECAS,
        broker="B",
    )
    result = reconcile(transactions=[_buy("110")], holdings=[h1, h2])
    assert result is not None
    assert result.units_from_holdings == Decimal("110")
    assert result.status is IntegrityStatus.RECONCILED


def test_reconcile_uses_latest_snapshot_date():
    old = _holding("50", on=date(2024, 3, 15))
    new = _holding("100", on=date(2025, 3, 15))
    result = reconcile(transactions=[_buy("100")], holdings=[new, old])
    assert result is not None
    assert result.units_from_holdings == Decimal("100")
    assert result.status is IntegrityStatus.RECONCILED


def test_tolerance_constant():
    assert Decimal("0.0001") == TOLERANCE
