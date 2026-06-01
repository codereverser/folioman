"""Cached, verified license — advisor-level (not investor-scoped).

The ``.license`` file is an ed25519-signed payload. This
model caches the parsed result so feature-gating doesn't re-verify the signature
on every request. Typically one active license per install (the advisor's).
"""

from __future__ import annotations

from django.db import models

from folioman_app.models.base import TimeStampedModel


class LicenseTier(models.TextChoices):
    FREE = "free", "Free"
    TAX_PACK = "tax_pack", "Tax Pack"
    PM_PRO = "pm_pro", "PM Pro"


class License(TimeStampedModel):
    # The raw signed license blob, kept for re-verification / audit.
    token = models.TextField(unique=True)
    tier = models.CharField(max_length=20, choices=LicenseTier.choices, default=LicenseTier.FREE)
    # Feature flags this license unlocks, e.g. ["tax_export", "unlimited_investors"].
    features = models.JSONField(default=list, blank=True)
    licensee = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(max_length=254, blank=True, default="")
    issued_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    # The verified, parsed payload in full (forward-compatible with new fields).
    payload = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.tier} ({self.licensee or 'unknown'})"
