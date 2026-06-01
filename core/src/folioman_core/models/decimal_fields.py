"""Decimal coercion for money and units — rejects float/bool and bad input.

Validators must raise ``ValueError`` (not ``TypeError``) so pydantic surfaces a
clean ``ValidationError`` for untrusted parser input rather than propagating a
raw exception.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator


def coerce_decimal(value: object) -> Decimal:
    # bool is an int subclass; reject it explicitly so True/False can't become 1/0.
    if isinstance(value, bool):
        msg = "bool is not a valid money/unit value"
        raise ValueError(msg)
    if isinstance(value, float):
        msg = "float literals are not allowed for money/units; use Decimal or string"
        raise ValueError(msg)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            msg = "decimal value cannot be empty"
            raise ValueError(msg)
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            msg = f"invalid decimal value: {value!r}"
            raise ValueError(msg) from exc
    msg = f"expected Decimal, int, or str, got {type(value).__name__}"
    raise ValueError(msg)


def coerce_optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return coerce_decimal(value)


DecimalField = Annotated[Decimal, BeforeValidator(coerce_decimal)]
OptionalDecimalField = Annotated[Decimal | None, BeforeValidator(coerce_optional_decimal)]
