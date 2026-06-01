"""Decimal/currency coercion fields — money-safety hardening (sessions 2.2/2.3)."""

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


def _tx(*, units="10", amount=None):
    # TRANSFER_IN keeps the buy/sell positive-units rule out of the way so these
    # tests exercise the decimal coercion layer specifically.
    return Transaction(
        security=_MF,
        date=date(2024, 6, 1),
        type=TransactionType.TRANSFER_IN,
        units=units,
        nav_or_price="100",
        amount=amount,
        source=TransactionSource.MANUAL,
    )


def test_int_coerces_to_decimal():
    assert _tx(units=5).units == Decimal(5)


def test_decimal_string_parsed():
    assert _tx(units="5.25").units == Decimal("5.25")


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_string_rejected(bad: str):
    with pytest.raises(ValidationError, match="empty"):
        _tx(units=bad)


def test_garbage_string_rejected():
    with pytest.raises(ValidationError, match="invalid decimal"):
        _tx(units="abc")


def test_float_rejected():
    with pytest.raises(ValidationError, match="float"):
        _tx(units=5.5)


def test_bool_rejected():
    with pytest.raises(ValidationError, match="bool"):
        _tx(units=True)


def test_wrong_type_rejected():
    with pytest.raises(ValidationError, match="expected Decimal"):
        _tx(units=[1, 2])


def test_optional_decimal_accepts_none_and_value():
    assert _tx(amount=None).amount is None
    assert _tx(amount="12.50").amount == Decimal("12.50")
