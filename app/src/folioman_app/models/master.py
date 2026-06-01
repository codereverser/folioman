"""Master / reference data shared across all investors (not investor-scoped).

A security is a global fact — one Reliance share is the same security for every
investor — so these tables carry no investor_id. The investor-scoped ledger
(Folio, Transaction, Holding) lives in the ledger module and FKs into Security here.

Field shapes mirror ``folioman_core.models.Security`` so the import services
can round-trip the pydantic value object into these rows. The DB field
is named ``security_type`` (the core attribute is ``type``); the mapping layer
translates.
"""

from __future__ import annotations

from django.db import models
from folioman_core.models import SecurityType

from folioman_app.models.base import TimeStampedModel

# Asset-class choices sourced from the core enum — single source of truth.
SECURITY_TYPE_CHOICES = [(t.value, t.name.replace("_", " ").title()) for t in SecurityType]


class AMC(TimeStampedModel):
    """Asset Management Company (mutual fund house)."""

    name = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        verbose_name = "AMC"
        verbose_name_plural = "AMCs"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Security(TimeStampedModel):
    """Canonical security reference. Mirrors ``folioman_core.models.Security``.

    Identity is (type, isin, symbol, exchange, amfi_code, currency). ISIN and
    amfi_code are partial-unique when present; symbol-only securities
    (crypto / FD) are deduped by app logic since they share an empty ISIN.
    """

    security_type = models.CharField(max_length=20, choices=SECURITY_TYPE_CHOICES)
    name = models.CharField(max_length=255)
    isin = models.CharField(max_length=12, blank=True, default="", db_index=True)
    symbol = models.CharField(max_length=32, blank=True, default="")
    exchange = models.CharField(max_length=16, blank=True, default="")
    currency = models.CharField(max_length=3, default="INR")
    amfi_code = models.CharField(max_length=16, blank=True, default="", db_index=True)
    amc = models.ForeignKey(
        AMC, null=True, blank=True, on_delete=models.PROTECT, related_name="securities"
    )
    # equity_oriented (112A eligibility), fund_type, coin_id, principal, etc.
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name_plural = "securities"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["isin"],
                condition=~models.Q(isin=""),
                name="uniq_security_isin",
            ),
            models.UniqueConstraint(
                fields=["amfi_code"],
                condition=~models.Q(amfi_code=""),
                name="uniq_security_amfi_code",
            ),
        ]

    def __str__(self) -> str:
        ident = self.isin or self.symbol or self.amfi_code or "?"
        return f"{self.name} [{self.security_type}:{ident}]"


class NAVHistory(TimeStampedModel):
    """Per-security price/NAV time series (MF NAV; price history for others).

    One row per (security, date). The (security, date) unique constraint also
    provides the index for as-of-date lookups (the core bisect-on-date logic).
    """

    security = models.ForeignKey(Security, on_delete=models.CASCADE, related_name="nav_history")
    date = models.DateField()
    nav = models.DecimalField(max_digits=20, decimal_places=6)
    source = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        verbose_name = "NAV history point"
        verbose_name_plural = "NAV history"
        ordering = ["security", "-date"]
        constraints = [
            models.UniqueConstraint(fields=["security", "date"], name="uniq_nav_security_date"),
        ]

    def __str__(self) -> str:
        return f"{self.security_id} @ {self.date}: {self.nav}"


class FXRate(TimeStampedModel):
    """Daily FX rate (base -> quote). For the deferred multi-currency valuation (v2)."""

    base_currency = models.CharField(max_length=3)
    quote_currency = models.CharField(max_length=3, default="INR")
    date = models.DateField()
    rate = models.DecimalField(max_digits=18, decimal_places=6)
    source = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        verbose_name = "FX rate"
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["base_currency", "quote_currency", "date"],
                name="uniq_fxrate_pair_date",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.base_currency}/{self.quote_currency} {self.date}: {self.rate}"
