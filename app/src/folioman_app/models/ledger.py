"""Investor-scoped ledger: Family, Investor, Folio, Transaction, Holding.

Every owned row carries an ``investor`` FK (auto-indexed) — the multi-tenant
scope. A `Family` groups investors for combined views; deleting a
family demotes its members to solo (SET_NULL), never their data.

Field shapes mirror folioman_core's Investor / Folio / Transaction / Holding so
the import + service layers round-trip the pydantic value objects into
these rows.

Every ``Family`` and ``Investor`` carries an ``owned_by`` FK to the advisor's
Django user. v1 is effectively single-advisor (one user owns everything),
but the column is set at creation time so that adding a second advisor later is
a pure config change — never an ambiguous backfill. Uniqueness (Family name,
Investor PAN) is scoped ``(owned_by, ...)`` so two advisors can independently
track the same family name / PAN.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from folioman_core.models import (
    FolioType,
    HoldingSource,
    TransactionSource,
    TransactionType,
)

from folioman_app.models.base import TimeStampedModel
from folioman_app.models.master import Security


# Choices sourced from the core enums (single source of truth).
def _choices(enum_cls) -> list[tuple[str, str]]:
    return [(m.value, m.name.replace("_", " ").title()) for m in enum_cls]


FOLIO_TYPE_CHOICES = _choices(FolioType)
TRANSACTION_TYPE_CHOICES = _choices(TransactionType)
TRANSACTION_SOURCE_CHOICES = _choices(TransactionSource)
HOLDING_SOURCE_CHOICES = _choices(HoldingSource)


class ValuationStatus(models.TextChoices):
    """Lifecycle of an investor's day-wise valuation computation."""

    PENDING = "pending", "Pending"  # queued; not started
    COMPUTING = "computing", "Computing"  # NAVs fetching / day-wise running
    READY = "ready", "Ready"  # series computed through today
    ERROR = "error", "Error"  # last attempt failed; awaiting retry


class Family(TimeStampedModel):
    """A group of investors for combined / aggregate views."""

    owned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="families"
    )
    name = models.CharField(max_length=255)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "families"
        ordering = ["name"]
        constraints = [
            # Unique per advisor: two advisors may both have a "Sharma Family".
            models.UniqueConstraint(fields=["owned_by", "name"], name="uniq_family_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Investor(TimeStampedModel):
    """A person whose investments are tracked — self, family member, HUF, client.

    Mirrors folioman_core.models.Investor. PAN is stored encrypted at rest; the
    SHA-256 ``pan_hash`` allows equality lookup / dedup without decrypting. The
    Fernet key lifecycle and the encrypt/decrypt helpers live in the security layer.
    """

    owned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="investors"
    )
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=254, blank=True, default="")
    is_huf = models.BooleanField(default=False)
    relation = models.CharField(max_length=20, blank=True, default="")
    family = models.ForeignKey(
        Family, null=True, blank=True, on_delete=models.SET_NULL, related_name="investors"
    )
    # PAN at rest: ciphertext + lookup hash.
    pan_encrypted = models.BinaryField(null=True, blank=True)
    pan_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)

    # Day-wise valuation status (driven by the background scheduler). The Investor
    # row is itself the durable work-list: the scheduler picks up rows that are
    # pending/computing (or error past next_attempt_at) and recomputes InvestorValue
    # from recompute_from. ``ready`` (the default) means the chart can render; a new
    # empty investor has nothing to compute and is ready.
    valuation_status = models.CharField(
        max_length=10, choices=ValuationStatus.choices, default=ValuationStatus.READY
    )
    valuation_recompute_from = models.DateField(null=True, blank=True)
    valuation_computed_through = models.DateField(null=True, blank=True)
    valuation_attempts = models.PositiveSmallIntegerField(default=0)
    valuation_next_attempt_at = models.DateTimeField(null=True, blank=True)
    valuation_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        constraints = [
            # One PAN per advisor. Partial — many investors (kids, etc.)
            # legitimately have no PAN entered.
            models.UniqueConstraint(
                fields=["owned_by", "pan_hash"],
                condition=~models.Q(pan_hash=""),
                name="uniq_investor_pan",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    # --- PAN handling -------------------------------------------------------
    @property
    def has_pan(self) -> bool:
        return bool(self.pan_hash)

    def set_pan(self, pan: str | None) -> None:
        """Encrypt + hash a PAN onto this instance (does not save). '' / None clears it."""
        from folioman_app.security.pan import encrypt_pan, pan_hash

        if not pan or not pan.strip():
            self.pan_encrypted = None
            self.pan_hash = ""
            return
        self.pan_encrypted = encrypt_pan(pan)
        self.pan_hash = pan_hash(pan)

    def get_pan(self) -> str | None:
        """Decrypt and return the PAN, or None if not set."""
        from folioman_app.security.pan import decrypt_pan

        if not self.pan_encrypted:
            return None
        return decrypt_pan(self.pan_encrypted)


class Folio(TimeStampedModel):
    """An MF folio or demat account belonging to an investor."""

    investor = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name="folios")
    folio_type = models.CharField(max_length=10, choices=FOLIO_TYPE_CHOICES)
    number = models.CharField(max_length=64)
    broker = models.CharField(max_length=64, blank=True, default="")
    amc_code = models.CharField(max_length=32, blank=True, default="")
    pan_kyc = models.BooleanField(default=False)

    class Meta:
        ordering = ["investor", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["investor", "number", "amc_code"],
                name="uniq_folio_investor_number_amc",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.number} ({self.folio_type})"


class Transaction(TimeStampedModel):
    """A single ledger event — input to FIFO and XIRR. Mirrors core Transaction.

    Sign convention follows the core: units / nav_or_price / fees are always
    non-negative; direction is carried by ``transaction_type``.
    """

    investor = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name="transactions")
    security = models.ForeignKey(Security, on_delete=models.PROTECT, related_name="transactions")
    folio = models.ForeignKey(
        Folio, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    date = models.DateField()
    transaction_type = models.CharField(max_length=16, choices=TRANSACTION_TYPE_CHOICES)
    units = models.DecimalField(max_digits=24, decimal_places=8)
    nav_or_price = models.DecimalField(max_digits=20, decimal_places=6)
    amount = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="INR")
    fx_rate_to_inr = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))
    fees = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    stamp_duty = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    # Buy-side brokerage / commission — part of cost of acquisition (Section 48),
    # folded into FIFO cost basis. Distinct from fees (sell-side STT) and
    # stamp_duty (transfer expense), neither of which enter cost basis.
    brokerage = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    source = models.CharField(max_length=20, choices=TRANSACTION_SOURCE_CHOICES)
    source_ref = models.CharField(max_length=128, blank=True, default="")
    # Verbatim transaction narration from the source statement, kept for an audit
    # trail (e.g. "SIP Installment", "Switch Out - To <scheme>"). Never used in any
    # computation or in the dedup key; TextField so a long narration can't truncate.
    narration = models.TextField(blank=True, default="")
    # Content hash set by the import service for idempotent re-imports; blank for
    # manual / corporate-action entries (no dedup). Set by the import service.
    dedup_key = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["investor", "date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["investor", "dedup_key"],
                condition=~models.Q(dedup_key=""),
                name="uniq_txn_investor_dedup",
            ),
        ]
        indexes = [
            models.Index(fields=["investor", "security", "date"], name="idx_txn_inv_sec_date"),
        ]

    def __str__(self) -> str:
        return f"{self.transaction_type} {self.units} @ {self.nav_or_price} ({self.date})"


class Holding(TimeStampedModel):
    """Point-in-time units observed from eCAS or manual entry. Mirrors core Holding."""

    investor = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name="holdings")
    security = models.ForeignKey(Security, on_delete=models.PROTECT, related_name="holdings")
    folio = models.ForeignKey(
        Folio, null=True, blank=True, on_delete=models.SET_NULL, related_name="holdings"
    )
    as_of_date = models.DateField()
    units = models.DecimalField(max_digits=24, decimal_places=8)
    value_observed = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    avg_cost_observed = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    source = models.CharField(max_length=20, choices=HOLDING_SOURCE_CHOICES)
    source_ref = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        ordering = ["investor", "security", "-as_of_date"]
        constraints = [
            # One snapshot per (investor, security, folio, date, source). SQL
            # treats NULLs as distinct, so this only enforces when folio is set...
            models.UniqueConstraint(
                fields=["investor", "security", "folio", "as_of_date", "source"],
                name="uniq_holding_snapshot",
            ),
            # ...and this partial constraint covers folio-less holdings
            # (crypto / FD / manual), which would otherwise slip past the above.
            models.UniqueConstraint(
                fields=["investor", "security", "as_of_date", "source"],
                condition=models.Q(folio__isnull=True),
                name="uniq_holding_snapshot_no_folio",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.units} units @ {self.as_of_date}"


class InvestorValue(TimeStampedModel):
    """One day's net-worth point for an investor — the persisted day-wise series.

    Computed by the background scheduler (``recompute_investor_valuation``) from the
    ledger + ``NAVHistory``; mirrors v1's ``PortfolioValue``. ``is_provisional`` marks
    the single point seeded synchronously at import from the statement's own reported
    value (as of its date), shown until the precise live-NAV series supersedes it.
    Family series = sum of members' rows by date.
    """

    investor = models.ForeignKey(Investor, on_delete=models.CASCADE, related_name="daily_values")
    date = models.DateField()
    invested_inr = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    value_inr = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    is_provisional = models.BooleanField(default=False)

    class Meta:
        ordering = ["investor", "date"]
        constraints = [
            models.UniqueConstraint(fields=["investor", "date"], name="uniq_investor_value_date"),
        ]
        indexes = [
            models.Index(fields=["investor", "date"], name="idx_investor_value_date"),
        ]

    def __str__(self) -> str:
        return f"{self.value_inr} @ {self.date}"
