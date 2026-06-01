"""Abstract base models shared across the app's model modules."""

from __future__ import annotations

from django.db import models


class TimeStampedModel(models.Model):
    """Adds created/updated audit timestamps. Abstract — no table of its own."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
