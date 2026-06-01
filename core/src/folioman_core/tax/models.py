"""Jurisdiction-neutral capital-gains types."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import Field

from folioman_core.models.base import DomainModel
from folioman_core.models.decimal_fields import DecimalField
from folioman_core.models.security import Security


class Term(StrEnum):
    SHORT = "short"
    LONG = "long"


class TaxYear(DomainModel):
    """Inclusive reporting period (meaning defined by ``TaxPolicy``)."""

    label: str = Field(min_length=1, max_length=16)
    start: date
    end: date


class Disposal(DomainModel):
    """One sell matched to a single acquisition lot (FIFO output)."""

    security: Security
    acquired_on: date
    sold_on: date
    units: DecimalField
    sale_price_per_unit: DecimalField
    cost_per_unit: DecimalField
    fees_allocated: DecimalField = Field(default=Decimal("0"))
    currency: str = Field(default="INR", min_length=3, max_length=3)


class GainLine(DomainModel):
    """Classified gain on one disposal after applying a ``TaxPolicy``."""

    disposal: Disposal
    term: Term
    proceeds: DecimalField
    adjusted_cost: DecimalField
    gain: DecimalField
    tax_year_label: str
    metadata: dict[str, Any] = Field(default_factory=dict)
