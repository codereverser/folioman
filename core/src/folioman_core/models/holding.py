"""Holding snapshots — observed positions, source-tagged."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from folioman_core.models.base import DomainModel
from folioman_core.models.decimal_fields import DecimalField, OptionalDecimalField
from folioman_core.models.security import Security


class HoldingSource(StrEnum):
    # A single consolidated CAS (issued by NSDL or CDSL — whichever holds the
    # investor's oldest demat account) lists demat holdings across BOTH
    # depositories, so there's one eCAS source, not one per depository.
    ECAS = "ecas"
    MANUAL = "manual"
    # An MF CAS scheme without full history (non-zero opening balance): its
    # closing balance is kept as a net-worth snapshot, not a (partial) ledger.
    CAS_PDF = "cas-pdf"
    # A position derived from the transaction ledger (current FIFO/net units),
    # not an observed snapshot. Used in-memory for valuation; never persisted.
    LEDGER = "ledger"


class Holding(DomainModel):
    """Point-in-time units observed from eCAS or manual entry."""

    security: Security
    as_of_date: date
    units: DecimalField
    value_observed: OptionalDecimalField = None
    avg_cost_observed: OptionalDecimalField = None
    source: HoldingSource
    source_ref: str = Field(default="", max_length=128)
    folio_number: str = Field(default="", max_length=64)
    broker: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def validate_holding_rules(self) -> Self:
        if self.units < 0:
            msg = "units cannot be negative"
            raise ValueError(msg)
        if self.value_observed is not None and self.value_observed < 0:
            msg = "value_observed cannot be negative"
            raise ValueError(msg)
        if self.avg_cost_observed is not None and self.avg_cost_observed < 0:
            msg = "avg_cost_observed cannot be negative"
            raise ValueError(msg)
        return self
