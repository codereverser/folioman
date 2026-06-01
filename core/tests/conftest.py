"""Shared pytest fixtures and helpers for folioman_core tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def dec(value: str) -> Decimal:
    """Parse a decimal string for money/unit assertions (never use float literals)."""
    return Decimal(value)


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory containing JSON/PDF test fixtures (redacted samples)."""
    return FIXTURES_DIR
