"""Integrity, licensing, and import-job models."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from folioman_app.models import (
    Folio,
    ImportJob,
    Investor,
    License,
    Security,
    SecurityIntegrityStatus,
)
from folioman_app.models.integrity import INTEGRITY_STATUS_CHOICES
from folioman_app.models.jobs import ImportJobStatus, ImportKind
from folioman_app.models.licensing import LicenseTier
from folioman_core.models import FolioType, SecurityType
from folioman_core.reconciliation import IntegrityStatus

pytestmark = pytest.mark.django_db


def _owner():
    from folioman_app.api.auth import get_local_user

    return get_local_user()


def _folio(inv: Investor) -> Folio:
    return Folio.objects.create(investor=inv, folio_type=FolioType.MF.value, number=f"F{inv.id}")


def _investor_and_security():
    inv = Investor.objects.create(owned_by=_owner(), name="Investor")
    sec = Security.objects.create(
        security_type=SecurityType.MF.value, name="Fund", amfi_code="122639"
    )
    return inv, sec


# --- SecurityIntegrityStatus ------------------------------------------------


def test_integrity_status_choices_match_core_enum():
    assert [value for value, _ in INTEGRITY_STATUS_CHOICES] == [s.value for s in IntegrityStatus]


def test_integrity_status_create_with_issues_json():
    inv, sec = _investor_and_security()
    row = SecurityIntegrityStatus.objects.create(
        investor=inv,
        security=sec,
        folio=_folio(inv),
        status=IntegrityStatus.MISMATCH.value,
        tax_safe=False,
        units_from_transactions=Decimal("100"),
        units_from_holdings=Decimal("90"),
        issues=[{"type": "unit_mismatch", "delta": "-10"}],
    )
    row.refresh_from_db()
    assert row.tax_safe is False
    assert row.issues[0]["type"] == "unit_mismatch"


def test_integrity_unique_per_investor_security_folio():
    inv, sec = _investor_and_security()
    folio = _folio(inv)
    SecurityIntegrityStatus.objects.create(
        investor=inv,
        security=sec,
        folio=folio,
        status=IntegrityStatus.RECONCILED.value,
        tax_safe=True,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        SecurityIntegrityStatus.objects.create(
            investor=inv, security=sec, folio=folio, status=IntegrityStatus.MISMATCH.value
        )


def test_same_security_different_status_per_investor():
    """A security can be tax-ready for one investor and a mismatch for another."""
    a = Investor.objects.create(owned_by=_owner(), name="A")
    b = Investor.objects.create(owned_by=_owner(), name="B")
    sec = Security.objects.create(
        security_type=SecurityType.EQUITY.value, name="Reliance", isin="INE002A01018"
    )
    SecurityIntegrityStatus.objects.create(
        investor=a,
        security=sec,
        folio=_folio(a),
        status=IntegrityStatus.RECONCILED.value,
        tax_safe=True,
    )
    SecurityIntegrityStatus.objects.create(
        investor=b,
        security=sec,
        folio=_folio(b),
        status=IntegrityStatus.MISMATCH.value,
        tax_safe=False,
    )
    assert SecurityIntegrityStatus.objects.filter(security=sec, tax_safe=True).count() == 1


# --- License ----------------------------------------------------------------


def test_license_defaults_to_free_tier():
    lic = License.objects.create(token="tok-1")
    assert lic.tier == LicenseTier.FREE
    assert lic.features == []
    assert lic.is_active is True


def test_license_with_features_and_expiry():
    lic = License.objects.create(
        token="tok-2",
        tier=LicenseTier.PM_PRO,
        features=["tax_export", "unlimited_investors"],
        licensee="ACME Advisors",
        expires_at=dt.datetime(2027, 4, 1, tzinfo=dt.UTC),
    )
    assert "tax_export" in lic.features
    assert lic.tier == LicenseTier.PM_PRO


def test_license_token_unique():
    License.objects.create(token="dup-token")
    with pytest.raises(IntegrityError), transaction.atomic():
        License.objects.create(token="dup-token")


# --- ImportJob --------------------------------------------------------------


def test_import_job_defaults_pending():
    inv = Investor.objects.create(owned_by=_owner(), name="Importer")
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CAS, filename="cams.pdf")
    assert job.status == ImportJobStatus.PENDING
    assert job.result == {}


def test_import_job_lifecycle_to_success():
    inv = Investor.objects.create(owned_by=_owner(), name="Importer")
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CAS)
    job.status = ImportJobStatus.SUCCESS
    job.result = {"securities_created": 3, "transactions_created": 0, "holdings_created": 5}
    job.finished_at = dt.datetime(2025, 6, 1, 12, 0, tzinfo=dt.UTC)
    job.save()
    job.refresh_from_db()
    assert job.status == ImportJobStatus.SUCCESS
    assert job.result["holdings_created"] == 5
