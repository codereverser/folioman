"""Apply corporate-action events to investor ledgers."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import (
    CorporateActionReference,
    Folio,
    ImportJob,
    Security,
    SecurityIntegrityStatus,
)
from folioman_app.models.jobs import ImportKind
from folioman_app.services.corporate_actions import apply_corporate_actions_to_folio
from folioman_app.tasks.import_csv import process_csv
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.corporate_actions import CorporateActionApplyEvent
from folioman_core.models import SecurityType
from folioman_core.models.cas import Depository, EcasAccountBlock, EcasHoldingLine, EcasStatement
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.security import Security as CoreSecurity

pytestmark = pytest.mark.django_db

_ALLCARGO = "INE418H01026"
_DEMAT = "1208160001234567"
_TRADEBOOK_HEADER = (
    "security_type,name,symbol,isin,date,transaction_type,units,price,folio_number,broker\n"
)


def _import_allcargo(inv, *, units="240"):
    row = (
        f"equity,Allcargo Logistics,ALLCARGO,{_ALLCARGO},2023-06-01,buy,{units},50,"
        f"{_DEMAT},ZERODHA\n"
    )
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")


def _ecas_allcargo(units: str) -> EcasStatement:
    security = CoreSecurity(type=SecurityType.EQUITY, name="Allcargo Logistics Ltd", isin=_ALLCARGO)
    return EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number=_DEMAT, broker="ZERODHA"),
                holdings=[EcasHoldingLine(security=security, units=units, value_observed="48000")],
            )
        ],
    )


def test_apply_bonus_reference_reconciles_allcargo(make_investor):
    inv = make_investor()
    _import_allcargo(inv)
    persist_ecas_statement(inv, _ecas_allcargo("960"), source_ref="ecas1")

    sec = Security.objects.get(isin=_ALLCARGO)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    ref = CorporateActionReference.objects.create(
        security=sec,
        isin=_ALLCARGO,
        symbol="ALLCARGO",
        exchange="NSE",
        ex_date=dt.date(2024, 1, 2),
        subject="Bonus 3:1",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=Decimal("4"),
        needs_review=False,
        source="NSE",
    )

    summary = apply_corporate_actions_to_folio(inv, folio, reference_ids=[ref.id])
    assert summary["created"] == 1

    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio=folio)
    assert status.status == "reconciled"
    assert status.units_from_transactions == Decimal("960")
    assert status.units_from_holdings == Decimal("960")
    assert not any(i["type"].startswith("corporate_action_") for i in status.issues)


def test_apply_bonus_idempotent_on_re_run(make_investor):
    inv = make_investor()
    _import_allcargo(inv)
    persist_ecas_statement(inv, _ecas_allcargo("960"), source_ref="ecas1")
    sec = Security.objects.get(isin=_ALLCARGO)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    ref = CorporateActionReference.objects.create(
        security=sec,
        isin=_ALLCARGO,
        symbol="ALLCARGO",
        exchange="NSE",
        ex_date=dt.date(2024, 1, 2),
        subject="Bonus 3:1",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=Decimal("4"),
        needs_review=False,
        source="NSE",
    )
    apply_corporate_actions_to_folio(inv, folio, reference_ids=[ref.id])
    again = apply_corporate_actions_to_folio(inv, folio, reference_ids=[ref.id])
    assert again.get("skipped") == "already_applied"
    assert inv.transactions.filter(folio=folio, transaction_type="bonus").count() == 1


def test_apply_merger_marks_pre_2016_acquisition_incomplete_on_persist(make_investor):
    """Merger rebasing preserves units but flags pre-2016 cost basis as incomplete."""
    inv = make_investor()
    old_isin = "INE001A01036"
    new_isin = "INE040A01034"
    row = f"equity,Old Co,OLDCO,{old_isin},2014-06-01,buy,10,100,{_DEMAT},ZERODHA\n"
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")

    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    events = [
        CorporateActionApplyEvent(
            kind=CorpActionType.MERGER,
            ex_date=dt.date(2020, 7, 1),
            security=CoreSecurity(
                type=SecurityType.EQUITY, name="New Co", isin=new_isin, symbol="NEWCO"
            ),
            merger_old_security=CoreSecurity(
                type=SecurityType.EQUITY, name="Old Co", isin=old_isin, symbol="OLDCO"
            ),
            merger_new_security=CoreSecurity(
                type=SecurityType.EQUITY, name="New Co", isin=new_isin, symbol="NEWCO"
            ),
            merger_ratio=Decimal("1"),
            source_ref="merger:pre2016",
        )
    ]
    apply_corporate_actions_to_folio(inv, folio, events=events)
    new_sec = Security.objects.get(isin=new_isin)
    txn = inv.transactions.get(security=new_sec, folio=folio)
    assert txn.date == dt.date(2014, 6, 1)
    assert txn.cost_basis_complete is False


def test_apply_merger_and_bonus_events_hdfcbank(make_investor):
    """Cross-ISIN merger + bonus via explicit events closes the HDFC golden gap."""
    inv = make_investor()
    hdfc_isin = "INE001A01036"
    hdfcbank_isin = "INE040A01034"
    row = f"equity,HDFC Ltd,HDFC,{hdfc_isin},2022-06-27,buy,50,2200,{_DEMAT},ZERODHA\n"
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")

    hdfcbank = CoreSecurity(
        type=SecurityType.EQUITY, name="HDFC Bank Ltd", isin=hdfcbank_isin, symbol="HDFCBANK"
    )
    persist_ecas_statement(
        inv,
        EcasStatement(
            depository=Depository.CDSL,
            statement_date=dt.date(2025, 9, 1),
            accounts=[
                EcasAccountBlock(
                    folio=CoreFolio(folio_type="demat", number=_DEMAT, broker="ZERODHA"),
                    holdings=[
                        EcasHoldingLine(security=hdfcbank, units="168", value_observed="300000")
                    ],
                )
            ],
        ),
        source_ref="ecas-hdfc",
    )

    hdfc_sec = Security.objects.get(isin=hdfc_isin)
    hdfcbank_sec = Security.objects.get(isin=hdfcbank_isin)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)

    events = [
        CorporateActionApplyEvent(
            kind=CorpActionType.MERGER,
            ex_date=dt.date(2023, 7, 13),
            security=CoreSecurity(
                type=SecurityType.EQUITY,
                name="HDFC Bank Ltd",
                isin=hdfcbank_isin,
                symbol="HDFCBANK",
            ),
            merger_old_security=CoreSecurity(
                type=SecurityType.EQUITY, name="HDFC Ltd", isin=hdfc_isin, symbol="HDFC"
            ),
            merger_new_security=CoreSecurity(
                type=SecurityType.EQUITY,
                name="HDFC Bank Ltd",
                isin=hdfcbank_isin,
                symbol="HDFCBANK",
            ),
            merger_ratio=Decimal("42") / Decimal("25"),
            source_ref="merger:hdfc",
        ),
        CorporateActionApplyEvent(
            kind=CorpActionType.BONUS,
            ex_date=dt.date(2025, 8, 26),
            security=CoreSecurity(
                type=SecurityType.EQUITY,
                name="HDFC Bank Ltd",
                isin=hdfcbank_isin,
                symbol="HDFCBANK",
            ),
            unit_multiplier=Decimal("2"),
            source_ref="bonus:1:1",
        ),
    ]
    apply_corporate_actions_to_folio(inv, folio, events=events)

    status = SecurityIntegrityStatus.objects.get(investor=inv, security=hdfcbank_sec, folio=folio)
    assert status.status == "reconciled"
    assert status.units_from_transactions == Decimal("168")
    # Original HDFC row should now point at HDFCBANK.
    assert not inv.transactions.filter(security=hdfc_sec, folio=folio).exists()
    assert inv.transactions.filter(security=hdfcbank_sec, folio=folio).count() >= 2
