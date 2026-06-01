"""Investor-scoped ledger models: family grouping, scoping, constraints."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from folioman_app.models import (
    Family,
    Folio,
    Holding,
    Investor,
    Security,
    Transaction,
)
from folioman_core.models import (
    FolioType,
    HoldingSource,
    SecurityType,
    TransactionSource,
    TransactionType,
)

pytestmark = pytest.mark.django_db


def _security(**kw) -> Security:
    kw.setdefault("security_type", SecurityType.MF.value)
    kw.setdefault("name", "Some Fund")
    kw.setdefault("amfi_code", "122639")
    return Security.objects.create(**kw)


def _owner():
    """The single local advisor user — owns every row these model tests create."""
    from folioman_app.api.auth import get_local_user

    return get_local_user()


def _investor(**kw) -> Investor:
    kw.setdefault("name", "Investor")
    kw.setdefault("owned_by", _owner())
    return Investor.objects.create(**kw)


def _family(**kw) -> Family:
    kw.setdefault("owned_by", _owner())
    return Family.objects.create(**kw)


def test_investor_in_family():
    fam = _family(name="Sharma Family")
    inv = _investor(name="Mr. Sharma", family=fam, relation="self")
    assert inv.family == fam
    assert list(fam.investors.all()) == [inv]


def test_deleting_family_demotes_investors_to_solo():
    """Deleting a family with 3 investors leaves all 3 with family=None."""
    fam = _family(name="Verma Family")
    investors = [_investor(name=f"Verma {i}", family=fam) for i in range(3)]
    fam.delete()
    for inv in investors:
        inv.refresh_from_db()
        assert inv.family is None
    assert Investor.objects.count() == 3  # data intact, only the grouping is gone


def test_cross_investor_scoping():
    """A transaction created for one investor is invisible to another's query."""
    sec = _security()
    a = _investor(name="Investor A")
    b = _investor(name="Investor B")
    Transaction.objects.create(
        investor=a,
        security=sec,
        date=dt.date(2025, 1, 1),
        transaction_type=TransactionType.BUY.value,
        units=Decimal("100"),
        nav_or_price=Decimal("10"),
        amount=Decimal("1000"),
        source=TransactionSource.CAS_PDF.value,
    )
    assert Transaction.objects.filter(investor=a).count() == 1
    assert Transaction.objects.filter(investor=b).count() == 0


def test_transaction_dedup_partial_unique():
    sec = _security()
    inv = _investor(name="Dedup Investor")

    def _txn(dedup_key: str) -> Transaction:
        return Transaction.objects.create(
            investor=inv,
            security=sec,
            date=dt.date(2025, 1, 1),
            transaction_type=TransactionType.BUY.value,
            units=Decimal("10"),
            nav_or_price=Decimal("10"),
            source=TransactionSource.CAS_PDF.value,
            dedup_key=dedup_key,
        )

    _txn("hash-abc")
    with pytest.raises(IntegrityError), transaction.atomic():
        _txn("hash-abc")  # same content hash for the same investor -> blocked

    # Blank dedup_key (manual entries) allows multiples.
    _txn("")
    _txn("")
    assert Transaction.objects.filter(investor=inv, dedup_key="").count() == 2


def test_same_dedup_key_allowed_across_investors():
    sec = _security()
    a = _investor(name="A")
    b = _investor(name="B")
    for inv in (a, b):
        Transaction.objects.create(
            investor=inv,
            security=sec,
            date=dt.date(2025, 1, 1),
            transaction_type=TransactionType.BUY.value,
            units=Decimal("1"),
            nav_or_price=Decimal("1"),
            source=TransactionSource.CAS_PDF.value,
            dedup_key="shared-hash",
        )
    assert Transaction.objects.filter(dedup_key="shared-hash").count() == 2


def test_folio_unique_per_investor_number_amc():
    inv = _investor(name="Folio Investor")
    Folio.objects.create(
        investor=inv, folio_type=FolioType.MF.value, number="12345/67", amc_code="PPFAS"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Folio.objects.create(
            investor=inv, folio_type=FolioType.MF.value, number="12345/67", amc_code="PPFAS"
        )


def test_holding_snapshot_unique():
    sec = _security()
    inv = _investor(name="Holding Investor")
    Holding.objects.create(
        investor=inv,
        security=sec,
        as_of_date=dt.date(2025, 6, 1),
        units=Decimal("100"),
        source=HoldingSource.MANUAL.value,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Holding.objects.create(
            investor=inv,
            security=sec,
            as_of_date=dt.date(2025, 6, 1),
            units=Decimal("105"),
            source=HoldingSource.MANUAL.value,
        )


def test_investor_pan_hash_partial_unique():
    _investor(name="Has PAN", pan_hash="a" * 64)
    with pytest.raises(IntegrityError), transaction.atomic():
        _investor(name="Dup PAN", pan_hash="a" * 64)
    # Multiple investors with no PAN are fine (kids, etc.).
    _investor(name="No PAN 1")
    _investor(name="No PAN 2")
    assert Investor.objects.filter(pan_hash="").count() == 2


def test_transaction_charges_roundtrip():
    sec = _security()
    inv = _investor(name="Charges Investor")
    txn = Transaction.objects.create(
        investor=inv,
        security=sec,
        date=dt.date(2025, 1, 1),
        transaction_type=TransactionType.SELL.value,
        units=Decimal("10.5"),
        nav_or_price=Decimal("123.4567"),
        amount=Decimal("1296.30"),
        fees=Decimal("1.25"),
        stamp_duty=Decimal("0.50"),
        source=TransactionSource.CAS_PDF.value,
    )
    txn.refresh_from_db()
    assert txn.units == Decimal("10.50000000")
    assert txn.nav_or_price == Decimal("123.456700")
    assert txn.fees == Decimal("1.25")
    assert txn.stamp_duty == Decimal("0.50")
    assert txn.fx_rate_to_inr == Decimal("1.000000")
