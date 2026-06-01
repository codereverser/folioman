"""Investor and folio models (renamed from Portfolio)."""

import pytest
from folioman_core.models import Folio, FolioType, Investor
from folioman_core.models.investor import normalize_folio_number
from pydantic import ValidationError


def test_investor_round_trip():
    investor = Investor(
        name="Self",
        email="investor@example.com",
        relation="self",
        folios=[
            Folio(folio_type=FolioType.MF, number="12345678/90", amc_code="PPFAS"),
            Folio(folio_type=FolioType.DEMAT, number="1208160001234567", broker="ZERODHA"),
        ],
    )
    restored = Investor.model_validate_json(investor.model_dump_json())
    assert restored == investor
    assert restored.relation == "self"


def test_folio_demat_requires_broker():
    with pytest.raises(ValidationError, match="broker"):
        Folio(folio_type=FolioType.DEMAT, number="1208160001234567")


def test_investor_normalizes_relation():
    investor = Investor(name="Spouse", email="spouse@example.com", relation=" Spouse ")
    assert investor.relation == "spouse"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The default sub-account: CAMS/CDSL print "/0", KFIN omits it -> unify.
        ("91013661625 / 0", "91013661625"),  # CAMS spacing
        ("91013661625/0", "91013661625"),  # CDSL eCAS
        ("91013661625", "91013661625"),  # KFIN (already canonical)
        ("12345/00", "12345"),  # zero-padded default suffix
        # Non-zero suffix is a real sub-account discriminator -> preserved.
        ("12124203 / 63", "12124203/63"),  # CAMS
        ("12124203/ 63", "12124203/63"),  # KFIN spacing
        ("12345/67", "12345/67"),
        # No suffix / plain demat account numbers pass through (whitespace stripped).
        ("1208160001234567", "1208160001234567"),
        ("  999  ", "999"),
    ],
)
def test_normalize_folio_number(raw, expected):
    assert normalize_folio_number(raw) == expected
