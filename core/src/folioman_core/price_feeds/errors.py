"""Shared errors for NAV/price feed adapters."""

from __future__ import annotations


class NAVFetchError(RuntimeError):
    """A NAV feed returned an error, or the response was unusable."""
