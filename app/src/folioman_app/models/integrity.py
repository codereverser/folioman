"""Per-investor, per-(security, folio) reconciliation status — the trust differentiator.

A ``SecurityIntegrityStatus`` row caches the result of reconciling an investor's
ledger transactions against their holding snapshots for one security **in one
folio**. Reconciliation is per-folio because completeness is per-folio: the same
scheme can be full-history in one folio and snapshot-only in another, and a sell
in one folio consumes only that folio's lots (see ``fifo.build_sell_disposals``).
Mirrors ``folioman_core.reconciliation.ReconciliationResult``; the reconcile task
computes and upserts these rows.
"""

from __future__ import annotations

from django.db import models
from folioman_core.reconciliation import IntegrityStatus

from folioman_app.models.base import TimeStampedModel
from folioman_app.models.ledger import Folio, Investor
from folioman_app.models.master import Security

INTEGRITY_STATUS_CHOICES = [(s.value, s.name.replace("_", " ").title()) for s in IntegrityStatus]


class SecurityIntegrityStatus(TimeStampedModel):
    investor = models.ForeignKey(
        Investor, on_delete=models.CASCADE, related_name="integrity_statuses"
    )
    security = models.ForeignKey(
        Security, on_delete=models.CASCADE, related_name="integrity_statuses"
    )
    # Reconciliation is per-folio: a scheme's completeness (and its lots) are
    # scoped to the folio/account that holds it.
    folio = models.ForeignKey(Folio, on_delete=models.CASCADE, related_name="integrity_statuses")
    status = models.CharField(max_length=20, choices=INTEGRITY_STATUS_CHOICES)
    # Whether this security's gains are safe to include in a tax export.
    tax_safe = models.BooleanField(default=False)
    units_from_transactions = models.DecimalField(
        max_digits=24, decimal_places=8, null=True, blank=True
    )
    units_from_holdings = models.DecimalField(
        max_digits=24, decimal_places=8, null=True, blank=True
    )
    # List of issue dicts (e.g. unit_mismatch with deltas); mirrors core issues.
    issues = models.JSONField(default=list, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "security integrity status"
        verbose_name_plural = "security integrity statuses"
        ordering = ["investor", "security", "folio"]
        constraints = [
            models.UniqueConstraint(
                fields=["investor", "security", "folio"],
                name="uniq_integrity_investor_security_folio",
            ),
        ]
        indexes = [
            # Speeds the tax-export query "tax-safe securities for investor X".
            models.Index(fields=["investor", "tax_safe"], name="idx_integrity_inv_taxsafe"),
        ]

    def __str__(self) -> str:
        return f"{self.investor_id}/{self.security_id}/{self.folio_id}: {self.status}"
