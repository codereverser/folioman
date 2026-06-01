"""ISO 4217 currency code coercion — 3-letter, upper-cased."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BeforeValidator


def normalize_currency(value: object) -> str:
    if value is None:
        return "INR"
    return str(value).strip().upper()


def validate_currency(value: str) -> str:
    if len(value) != 3 or not value.isalpha():
        msg = "currency must be a 3-letter ISO 4217 code"
        raise ValueError(msg)
    return value


CurrencyField = Annotated[
    str, BeforeValidator(normalize_currency), AfterValidator(validate_currency)
]
