"""Master / reference models: creation, constraints, choices."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from folioman_app.models import AMC, FXRate, NAVHistory, Security
from folioman_app.models.master import SECURITY_TYPE_CHOICES
from folioman_core.models import SecurityType

pytestmark = pytest.mark.django_db


def test_security_type_choices_sourced_from_core_enum():
    assert [value for value, _ in SECURITY_TYPE_CHOICES] == [t.value for t in SecurityType]


def test_create_mf_security_with_amc_and_metadata():
    amc = AMC.objects.create(name="PPFAS Mutual Fund", code="PPFAS")
    sec = Security.objects.create(
        security_type=SecurityType.MF.value,
        name="Parag Parikh Flexi Cap",
        amfi_code="122639",
        amc=amc,
        metadata={"equity_oriented": True, "fund_type": "EQUITY"},
    )
    assert sec.amc.name == "PPFAS Mutual Fund"
    assert sec.metadata["equity_oriented"] is True
    # JSONField key lookup works (used by 112A eligibility queries).
    assert Security.objects.filter(metadata__equity_oriented=True).count() == 1


def test_isin_partial_unique_blocks_duplicate():
    Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Security.objects.create(
            security_type=SecurityType.EQUITY.value, name="Reliance Dup", isin="INE002A01018"
        )


def test_empty_isin_allows_multiple_rows():
    """The ISIN uniqueness is partial — many securities legitimately have no ISIN."""
    Security.objects.create(security_type=SecurityType.CRYPTO.value, name="Bitcoin", symbol="BTC")
    Security.objects.create(security_type=SecurityType.CRYPTO.value, name="Ethereum", symbol="ETH")
    assert Security.objects.filter(isin="").count() == 2


def test_amfi_code_partial_unique_blocks_duplicate():
    Security.objects.create(security_type=SecurityType.MF.value, name="Fund A", amfi_code="100001")
    with pytest.raises(IntegrityError), transaction.atomic():
        Security.objects.create(
            security_type=SecurityType.MF.value, name="Fund A dup", amfi_code="100001"
        )


def test_navhistory_unique_per_security_date():
    sec = Security.objects.create(
        security_type=SecurityType.MF.value, name="Fund", amfi_code="200001"
    )
    NAVHistory.objects.create(security=sec, date=dt.date(2025, 6, 1), nav=Decimal("75.1234"))
    with pytest.raises(IntegrityError), transaction.atomic():
        NAVHistory.objects.create(security=sec, date=dt.date(2025, 6, 1), nav=Decimal("75.5"))


def test_navhistory_decimal_roundtrip():
    sec = Security.objects.create(
        security_type=SecurityType.CRYPTO.value, name="Bitcoin", symbol="BTC"
    )
    NAVHistory.objects.create(security=sec, date=dt.date(2025, 6, 1), nav=Decimal("8500000.123456"))
    stored = NAVHistory.objects.get(security=sec, date=dt.date(2025, 6, 1))
    assert stored.nav == Decimal("8500000.123456")


def test_fxrate_unique_per_pair_date():
    FXRate.objects.create(
        base_currency="USD", quote_currency="INR", date=dt.date(2025, 6, 1), rate=Decimal("83.45")
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        FXRate.objects.create(
            base_currency="USD",
            quote_currency="INR",
            date=dt.date(2025, 6, 1),
            rate=Decimal("83.50"),
        )
