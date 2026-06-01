"""Security identity — shared between parsers, FIFO, and (later) Django ORM."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from folioman_core.models.base import DomainModel
from folioman_core.models.currency_fields import CurrencyField

# Structural check only (2 letters + 9 alphanumerics + 1 digit). The Luhn check
# digit is validated by casparser-isin at the parser boundary.
_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class SecurityType(StrEnum):
    """Asset class; mirrors Django Security.SECURITY_TYPES (v1 subset + v2 hook)."""

    MF = "mf"
    EQUITY = "equity"
    ETF = "etf"
    BOND = "bond"
    FD = "fd"
    CRYPTO = "crypto"
    FOREIGN_EQUITY = "foreign_equity"


class Security(DomainModel):
    """Canonical security reference used across core and import pipelines.

    Frozen value object: immutable, **hashable AND equal on its identity
    fields only** (type / isin / symbol / exchange / amfi_code / currency).
    ``name`` and ``metadata`` are descriptive — they're excluded from both
    ``__hash__`` and ``__eq__`` so the same scheme with a slightly drifted
    name (e.g. ``"ABC Fund Growth"`` vs ``"ABC Fund - Growth"``) lands in the
    same FIFO bucket / dict slot. Name drift across CAS, eCAS, and manual
    sources is a real risk at import boundaries — keying on identity is the
    fix.

    Hash/eq alignment is load-bearing: ``fifo.build_sell_disposals`` and
    ``schedule_112a.compute_schedule_112a`` both use ``Security`` as a dict
    key. If ``__eq__`` were stricter than ``__hash__``, two same-hash-but-not-
    equal objects would coexist as separate dict entries — silently splitting
    cost-basis buckets and dropping LTCG rows from Schedule 112A.
    """

    model_config = ConfigDict(frozen=True)

    type: SecurityType
    name: str = Field(min_length=1, max_length=255)
    isin: str = ""
    symbol: str = Field(default="", max_length=32)
    exchange: str = Field(default="", max_length=16)
    currency: CurrencyField = "INR"
    amfi_code: str = Field(default="", max_length=16)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("isin", mode="before")
    @classmethod
    def normalize_isin(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip().upper()

    @field_validator("isin")
    @classmethod
    def validate_isin(cls, value: str) -> str:
        if not value:
            return value
        if len(value) != 12:
            msg = "ISIN must be exactly 12 characters when provided"
            raise ValueError(msg)
        if not _ISIN_PATTERN.match(value):
            msg = "ISIN must be 2 letters + 9 alphanumerics + 1 check digit"
            raise ValueError(msg)
        return value

    @field_validator("symbol", "exchange", "amfi_code", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @model_validator(mode="after")
    def validate_type_specific_rules(self) -> Self:
        if self.type is SecurityType.MF:
            if not self.amfi_code and not self.isin:
                msg = "mutual fund security requires amfi_code or isin"
                raise ValueError(msg)
        elif self.type in (SecurityType.EQUITY, SecurityType.ETF, SecurityType.BOND):
            if not self.symbol and not self.isin:
                msg = f"{self.type.value} security requires symbol or isin"
                raise ValueError(msg)
        elif self.type is SecurityType.CRYPTO:
            coin_id = self.metadata.get("coin_id")
            if not self.symbol and not coin_id:
                msg = "crypto security requires symbol or metadata['coin_id']"
                raise ValueError(msg)
        elif (
            self.type is SecurityType.FD
            and not self.metadata.get("principal")
            and not self.metadata.get("account_ref")
        ):
            msg = "fixed deposit security requires metadata['principal'] or metadata['account_ref']"
            raise ValueError(msg)
        return self

    def _identity(self) -> tuple:
        return (self.type, self.isin, self.symbol, self.exchange, self.amfi_code, self.currency)

    def __hash__(self) -> int:
        return hash(self._identity())

    def __eq__(self, other: object) -> bool:
        # Identity-only equality (matches __hash__). Returning NotImplemented
        # for non-Security ``other`` lets Python try the reflected op / fall
        # back to ``is`` so ``Security == "string"`` behaves like any other
        # cross-type comparison instead of raising.
        if not isinstance(other, Security):
            return NotImplemented
        return self._identity() == other._identity()
