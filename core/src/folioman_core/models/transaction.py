"""Ledger transactions — full history, source-tagged."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from folioman_core.models.base import DomainModel
from folioman_core.models.currency_fields import CurrencyField
from folioman_core.models.decimal_fields import DecimalField, OptionalDecimalField
from folioman_core.models.security import Security


class TransactionType(StrEnum):
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    BONUS = "bonus"
    SPLIT = "split"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


class TransactionSource(StrEnum):
    CAS_PDF = "cas-pdf"
    MANUAL = "manual"
    CSV_IMPORT = "csv-import"
    CORPORATE_ACTION = "corporate-action"


class Transaction(DomainModel):
    """A single ledger event; suitable input for FIFO and XIRR.

    Sign convention: ``units``, ``nav_or_price`` and ``fees`` are always
    non-negative. Direction is carried by ``type`` — never by sign:
    BUY / TRANSFER_IN / BONUS add to the position; SELL / TRANSFER_OUT reduce
    it. FIFO must branch on ``type`` and never infer direction
    from a negative quantity.

    Corporate actions: a BONUS row carries the bonus units received at zero
    price (``nav_or_price == 0``). SPLIT row semantics (ratio vs. resulting
    unit delta) are defined and applied by ``corporate_actions``;
    treat raw SPLIT rows as opaque until then.
    """

    security: Security
    date: date
    type: TransactionType
    units: DecimalField
    nav_or_price: DecimalField
    amount: OptionalDecimalField = None
    currency: CurrencyField = "INR"
    fx_rate_to_inr: DecimalField = Field(default=Decimal("1"))
    fees: DecimalField = Field(default=Decimal("0"))
    # Buy-side stamp duty (or similar acquisition-side transfer charge). Stored
    # per-lot and allocated to disposals as a transfer expense on sells — does
    # NOT enter cost basis. Mirrors casparser's MergedTransaction.stamp_duty.
    stamp_duty: DecimalField = Field(default=Decimal("0"))
    # Buy-side brokerage / commission. UNLIKE `fees` (sell-side STT) and
    # `stamp_duty` (transfer expense), this IS part of the cost of acquisition
    # (Section 48) and is folded into the lot's effective per-unit cost by FIFO.
    # Zero for CAS-sourced MF rows; populated for manual / CSV equity entries.
    brokerage: DecimalField = Field(default=Decimal("0"))
    # Exact lot cost of acquisition preserved through a corporate action. A
    # split/merger rewrites `units` and per-unit `nav_or_price`, but `total / units`
    # is a repeating decimal for an indivisible ratio, so per-unit can't carry the
    # lot cost exactly. When set, FIFO uses this as the lot's TOTAL cost (apportioned
    # by units fraction on a sell) instead of `units * nav_or_price + brokerage`, and
    # `nav_or_price` becomes a best-effort per-unit for display. None on ordinary
    # rows → FIFO computes cost the usual way (no behaviour change).
    cost_total: OptionalDecimalField = None
    source: TransactionSource
    source_ref: str = Field(default="", max_length=128)
    folio_number: str = Field(default="", max_length=64)
    broker: str = Field(default="", max_length=64)
    # Django row pk when round-tripping through the apply engine; ignored by FIFO.
    ledger_id: int | None = None

    @model_validator(mode="after")
    def validate_transaction_rules(self) -> Self:
        if self.units < 0:
            msg = "units cannot be negative; direction is set by `type`"
            raise ValueError(msg)
        if self.nav_or_price < 0:
            msg = "nav_or_price cannot be negative"
            raise ValueError(msg)
        if self.type in (TransactionType.BUY, TransactionType.SELL) and self.units <= 0:
            msg = "buy and sell transactions require positive units"
            raise ValueError(msg)
        if self.type is TransactionType.DIVIDEND and self.units <= 0 and self.amount is None:
            msg = "dividend requires amount when units are zero"
            raise ValueError(msg)
        if self.fees < 0:
            msg = "fees cannot be negative"
            raise ValueError(msg)
        if self.stamp_duty < 0:
            msg = "stamp_duty cannot be negative"
            raise ValueError(msg)
        if self.brokerage < 0:
            msg = "brokerage cannot be negative"
            raise ValueError(msg)
        if self.cost_total is not None and self.cost_total < 0:
            msg = "cost_total cannot be negative"
            raise ValueError(msg)
        if self.fx_rate_to_inr <= 0:
            msg = "fx_rate_to_inr must be positive"
            raise ValueError(msg)
        return self
