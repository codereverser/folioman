"""Transaction domain model."""

from datetime import date
from decimal import Decimal

import pytest
from folioman_core.models import (
    Security,
    SecurityType,
    Transaction,
    TransactionSource,
    TransactionType,
)
from pydantic import ValidationError

_MF = Security(type=SecurityType.MF, name="Sample MF", amfi_code="122639")


@pytest.mark.parametrize(
    "tx_type",
    [
        TransactionType.BUY,
        TransactionType.SELL,
        TransactionType.DIVIDEND,
        TransactionType.BONUS,
        TransactionType.SPLIT,
        TransactionType.TRANSFER_IN,
        TransactionType.TRANSFER_OUT,
    ],
)
def test_transaction_types_round_trip(tx_type: TransactionType):
    units = "0" if tx_type is TransactionType.DIVIDEND else "10.0000"
    amount = "500.00" if tx_type is TransactionType.DIVIDEND else "1000.00"
    tx = Transaction(
        security=_MF,
        date=date(2024, 6, 1),
        type=tx_type,
        units=units,
        nav_or_price="100.0000",
        amount=amount,
        source=TransactionSource.MANUAL,
    )
    restored = Transaction.model_validate_json(tx.model_dump_json())
    assert restored == tx
    assert isinstance(restored.units, Decimal)


def test_transaction_coerces_int_to_decimal():
    tx = Transaction(
        security=_MF,
        date=date(2024, 6, 1),
        type=TransactionType.BUY,
        units=100,
        nav_or_price=50,
        source=TransactionSource.CAS_PDF,
    )
    assert tx.units == Decimal(100)


def test_transaction_rejects_float_units():
    with pytest.raises(ValidationError, match="float"):
        Transaction(
            security=_MF,
            date=date(2024, 6, 1),
            type=TransactionType.BUY,
            units=10.5,
            nav_or_price="100",
            source=TransactionSource.MANUAL,
        )


def test_transaction_buy_requires_positive_units():
    with pytest.raises(ValidationError, match="positive units"):
        Transaction(
            security=_MF,
            date=date(2024, 6, 1),
            type=TransactionType.BUY,
            units="0",
            nav_or_price="100",
            source=TransactionSource.MANUAL,
        )


def test_transaction_dividend_requires_amount_when_zero_units():
    with pytest.raises(ValidationError, match="dividend requires amount"):
        Transaction(
            security=_MF,
            date=date(2024, 6, 1),
            type=TransactionType.DIVIDEND,
            units="0",
            nav_or_price="0",
            source=TransactionSource.CORPORATE_ACTION,
        )


def test_transaction_rejects_negative_units_direction_via_type():
    # negative units never allowed — direction is carried by `type`, not sign
    with pytest.raises(ValidationError, match="units cannot be negative"):
        Transaction(
            security=_MF,
            date=date(2024, 6, 1),
            type=TransactionType.TRANSFER_IN,
            units="-5",
            nav_or_price="100",
            source=TransactionSource.MANUAL,
        )


def test_transaction_rejects_negative_price():
    with pytest.raises(ValidationError, match="nav_or_price cannot be negative"):
        Transaction(
            security=_MF,
            date=date(2024, 6, 1),
            type=TransactionType.BONUS,
            units="5",
            nav_or_price="-1",
            source=TransactionSource.CORPORATE_ACTION,
        )


def test_transaction_rejects_negative_fees():
    with pytest.raises(ValidationError, match="fees cannot be negative"):
        Transaction(
            security=_MF,
            date=date(2024, 6, 1),
            type=TransactionType.BUY,
            units="10",
            nav_or_price="100",
            fees="-1",
            source=TransactionSource.MANUAL,
        )


def test_transaction_rejects_negative_brokerage():
    with pytest.raises(ValidationError, match="brokerage cannot be negative"):
        Transaction(
            security=_MF,
            date=date(2024, 6, 1),
            type=TransactionType.BUY,
            units="10",
            nav_or_price="100",
            brokerage="-1",
            source=TransactionSource.MANUAL,
        )


def test_transaction_rejects_nonpositive_fx_rate():
    with pytest.raises(ValidationError, match="fx_rate_to_inr must be positive"):
        Transaction(
            security=_MF,
            date=date(2024, 6, 1),
            type=TransactionType.BUY,
            units="10",
            nav_or_price="100",
            fx_rate_to_inr="0",
            source=TransactionSource.MANUAL,
        )
