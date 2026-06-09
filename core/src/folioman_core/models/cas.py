"""Parsed CAS / eCAS statement views — targets for parser output."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from folioman_core.models.base import DomainModel
from folioman_core.models.decimal_fields import DecimalField, OptionalDecimalField
from folioman_core.models.investor import Folio
from folioman_core.models.security import Security
from folioman_core.models.transaction import TransactionType


class Depository(StrEnum):
    NSDL = "NSDL"
    CDSL = "CDSL"


class CasInvestorIdentity(DomainModel):
    """Owner identity extracted from a CAS for investor resolution.

    Carries the **full** PAN (unlike the statement models, which keep only a
    masked PAN for display). This is PII: it must flow only into the encrypted
    ``Investor.pan_*`` columns and must never be logged or serialized into an
    error message / job result. ``pan == ""`` means the statement carried none.
    """

    name: str = ""
    email: str = ""
    pan: str = ""


class MfCasLineItem(DomainModel):
    """One transaction line inside an MF CAS scheme block.

    ``fees`` carries the transfer expenses (STT + stamp duty on the same date as
    a sell) that CAS PDFs ship as separate rows; the import wrapper folds them
    here so downstream consumers see one consistent ``Transaction.fees`` value.
    """

    date: date
    transaction_type: TransactionType
    units: DecimalField
    nav: DecimalField
    amount: OptionalDecimalField = None
    fees: DecimalField = Field(default=Decimal("0"))
    stamp_duty: DecimalField = Field(default=Decimal("0"))
    description: str = ""
    # Running unit balance after this transaction (from the CAS). Statement-
    # independent, so it disambiguates genuinely-distinct same-day/same-amount
    # rows (e.g. two SWP redemptions) in the transaction dedup key. ``None`` on
    # rows the CAS doesn't carry a balance for (e.g. dividend payouts).
    balance: OptionalDecimalField = None


class MfCasSchemeBlock(DomainModel):
    """Scheme + folio block from a CAMS/KFin CAS PDF.

    ``opening_units`` is the balance carried in at ``statement_from``. The block
    is a complete cost-basis history only if that opening chains gap-free onto
    what's already on the ledger (zero for a first import) — judged by
    ``parser.scheme_history_gap`` with the prior ledger balance supplied by the
    caller.
    """

    folio: Folio
    security: Security
    transactions: list[MfCasLineItem] = Field(default_factory=list)
    opening_units: OptionalDecimalField = None
    closing_units: OptionalDecimalField = None
    # The statement's own reported valuation as of ``statement_to`` (casparser's
    # ``Scheme.valuation``): NAV + market value + cost. Lets the import show a real
    # value immediately ("provisional"), before live NAVs are fetched.
    closing_nav: OptionalDecimalField = None
    closing_value: OptionalDecimalField = None
    closing_cost: OptionalDecimalField = None
    closing_value_date: date | None = None
    unsupported_transaction: bool = False


class MfCasStatement(DomainModel):
    """Full parsed mutual-fund CAS PDF."""

    investor_name: str = ""
    investor_email: str = ""
    pan_masked: str = Field(default="", max_length=16)
    statement_from: date | None = None
    statement_to: date | None = None
    file_hash: str = Field(default="", max_length=128)
    schemes: list[MfCasSchemeBlock] = Field(default_factory=list)


class EcasHoldingLine(DomainModel):
    """Single holding row from an NSDL/CDSL eCAS.

    ``folio`` overrides the account-level demat folio for this line. Demat-held
    mutual funds carry their own RTA folio (the eCAS prints it per holding), which
    is the same identity as the MF CAS ledger — so MF lines set it and equities/
    bonds leave it ``None`` (they belong to the demat account folio).
    """

    security: Security
    units: DecimalField
    value_observed: OptionalDecimalField = None
    avg_cost_observed: OptionalDecimalField = None
    folio: Folio | None = None


class EcasAccountBlock(DomainModel):
    """One demat account (broker) within an eCAS."""

    folio: Folio
    holdings: list[EcasHoldingLine] = Field(default_factory=list)


class EcasStatement(DomainModel):
    """Full parsed NSDL or CDSL eCAS PDF."""

    depository: Depository
    statement_date: date
    investor_name: str = ""
    pan_masked: str = Field(default="", max_length=16)
    file_hash: str = Field(default="", max_length=128)
    accounts: list[EcasAccountBlock] = Field(default_factory=list)
