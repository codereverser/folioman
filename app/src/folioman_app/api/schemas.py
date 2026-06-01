"""Request/response schemas for the Ninja API.

Never expose PAN material — only a ``has_pan`` boolean. PAN comes IN as plaintext
(``pan``) and is encrypted server-side; it is never returned.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ninja import Schema
from pydantic import Field


class InvestorIn(Schema):
    name: str
    email: str = ""
    is_huf: bool = False
    relation: str = ""
    family_id: int | None = None
    pan: str | None = None  # plaintext in; encrypted server-side; never returned


class InvestorUpdate(Schema):
    # All optional — PATCH applies only the fields present in the request body.
    name: str | None = None
    email: str | None = None
    is_huf: bool | None = None
    relation: str | None = None
    family_id: int | None = None  # present+null clears the family (move to solo)
    pan: str | None = None  # present+null/'' clears the PAN


class InvestorOut(Schema):
    id: int
    name: str
    email: str
    is_huf: bool
    relation: str
    family_id: int | None
    has_pan: bool
    created_at: datetime
    updated_at: datetime


class FolioOut(Schema):
    id: int
    folio_type: str
    number: str
    broker: str
    amc_code: str
    pan_kyc: bool


class FamilyIn(Schema):
    name: str


class FamilyOut(Schema):
    id: int
    name: str
    investor_count: int
    created_at: datetime


class FamilyDetailOut(FamilyOut):
    investors: list[InvestorOut]


class AssetMixRow(Schema):
    security_type: str
    value_inr: Decimal


class HoldingValueRow(Schema):
    security_id: int
    name: str
    security_type: str
    units: Decimal
    value_inr: Decimal | None


class FamilyAggregateOut(Schema):
    family_id: int
    as_of: date
    investor_count: int
    total_inr: Decimal
    asset_mix: list[AssetMixRow]
    top_holdings: list[HoldingValueRow]
    stale_count: int
    xirr: float | None = None  # annualized rate as a fraction (0.1849 = 18.49%)


class InvestorSummaryOut(Schema):
    """Per-investor headline numbers for the roster row."""

    investor_id: int
    as_of: date
    total_inr: Decimal
    holdings_count: int  # securities currently held (units > 0)
    tax_ready_count: int  # of those, integrity-verified for the tax export
    needs_attention_count: int  # mismatches awaiting resolution
    snapshot_count: int  # snapshot-only (no transaction history)
    stale_count: int  # held but unpriced (no NAV on/before as_of)
    last_import_at: datetime | None
    xirr: float | None = None  # annualized rate as a fraction (0.1849 = 18.49%)


class ValueSeriesPoint(Schema):
    """One sampled date in the net-worth time series."""

    date: date
    value_inr: Decimal  # held units priced at the latest NAV on/before the date
    invested_inr: Decimal  # FIFO cost basis of the units still held (>= 0)
    stale: bool  # at least one held security had no price on/before the date


class ValueSeriesOut(Schema):
    """Reconstructed net-worth-over-time series for an investor or family."""

    investor_id: int | None = None
    family_id: int | None = None
    start: date
    end: date
    granularity: str
    points: list[ValueSeriesPoint]


class ImportJobOut(Schema):
    id: int
    investor_id: int
    kind: str
    status: str
    filename: str
    source_ref: str
    result: dict
    error: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class TransactionIn(Schema):
    """A single manually-entered transaction (security identified inline)."""

    # Security identification (upserted on the fly).
    security_type: str
    name: str
    symbol: str = ""
    isin: str = ""
    amfi_code: str = ""
    coin_id: str = ""
    principal: str = ""
    # Folio the security is held under — required (no folio-less entries). For
    # equity/demat this is the demat account number (matches eCAS exactly) and a
    # broker is required; for MF it's the AMC folio number.
    folio_number: str
    broker: str = ""
    # Transaction fields.
    date: date
    transaction_type: str
    units: Decimal
    price: Decimal
    amount: Decimal | None = None
    fees: Decimal = Decimal("0")
    stamp_duty: Decimal = Decimal("0")
    brokerage: Decimal = Decimal("0")
    currency: str = "INR"
    narration: str = ""  # optional free-text note for a manual entry


class TransactionOut(Schema):
    id: int
    investor_id: int
    security_id: int
    folio_id: int | None
    date: date
    transaction_type: str
    units: Decimal
    nav_or_price: Decimal
    amount: Decimal | None
    fees: Decimal
    stamp_duty: Decimal
    brokerage: Decimal
    currency: str
    source: str
    narration: str  # verbatim source-statement narration (audit trail)


class SecurityRef(Schema):
    id: int
    name: str
    isin: str
    symbol: str
    security_type: str


class FolioRef(Schema):
    id: int
    number: str
    broker: str
    folio_type: str


class IntegrityStatusOut(Schema):
    security: SecurityRef
    folio: FolioRef
    status: str
    tax_safe: bool
    units_from_transactions: Decimal | None
    units_from_holdings: Decimal | None
    issues: list[dict]
    last_reconciled_at: datetime | None


class Schedule112ARequest(Schema):
    fy: str = Field(pattern=r"^\d{4}-\d{2}$", description="India FY, e.g. 2024-25")
    include_unreconciled: bool = False


class Schedule112AResponse(Schema):
    fy: str
    include_unreconciled: bool
    row_count: int
    columns: list[str]
    rows: list[dict[str, str]]
