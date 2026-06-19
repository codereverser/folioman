"""Reconciliation-driven corporate-action detection (E10.3 wiring)."""

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
from folioman_app.tasks.import_csv import process_csv
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_app.tasks.reconcile import reconcile_security_folio
from folioman_core.corporate_action_subject import CorpActionType
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


def _import_allcargo_tradebook(inv, *, units="240"):
    # Buy precedes the bonus ex-date (2024-02-01) so the position is entitled to it.
    row = (
        f"equity,Allcargo Logistics,ALLCARGO,{_ALLCARGO},2024-01-15,buy,{units},50,"
        f"{_DEMAT},ZERODHA\n"
    )
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    return process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")


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


def _seed_bonus_3_1(security: Security) -> CorporateActionReference:
    return CorporateActionReference.objects.create(
        security=security,
        isin=_ALLCARGO,
        symbol="ALLCARGO",
        exchange="NSE",
        ex_date=dt.date(2024, 2, 1),
        subject="Bonus 3:1",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=Decimal("4"),
        needs_review=False,
        source="NSE",
    )


def test_allcargo_bonus_gap_auto_suggests(make_investor):
    """Complete tradebook net 240 vs eCAS 960 → high-confidence Bonus 3:1 suggestion."""
    inv = make_investor()
    _import_allcargo_tradebook(inv)
    persist_ecas_statement(inv, _ecas_allcargo("960"), source_ref="ecas1")

    sec = Security.objects.get(isin=_ALLCARGO)
    ref = _seed_bonus_3_1(sec)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    reconcile_security_folio(inv, sec, folio)

    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec)
    ca = [i for i in status.issues if i["type"] == "corporate_action_suggestion"]
    assert len(ca) == 1
    assert ca[0]["confidence"] == "high"
    assert ca[0]["reference_ids"] == [ref.id]
    assert ca[0]["events"][0]["subject"] == "Bonus 3:1"
    assert ca[0]["events"][0]["unit_multiplier"] == "4"


def test_orphan_sell_never_auto_suggests_bonus(make_investor):
    """Orphan-sell ledger is flagged manual only — ratio matching is unreliable."""
    inv = make_investor()
    header = _TRADEBOOK_HEADER
    # Buy 5, sell 15 → net -10, orphan overhang 10.
    rows = (
        f"equity,Allcargo Logistics,ALLCARGO,{_ALLCARGO},2024-01-10,buy,5,50,{_DEMAT},ZERODHA\n"
        f"equity,Allcargo Logistics,ALLCARGO,{_ALLCARGO},2024-06-01,sell,15,60,{_DEMAT},ZERODHA\n"
    )
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (header + rows).encode(), "")
    persist_ecas_statement(inv, _ecas_allcargo("168"), source_ref="ecas1")

    sec = Security.objects.get(isin=_ALLCARGO)
    _seed_bonus_3_1(sec)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    reconcile_security_folio(inv, sec, folio)

    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec)
    assert not any(i["type"] == "corporate_action_suggestion" for i in status.issues)
    manual = [i for i in status.issues if i["type"] == "corporate_action_manual"]
    assert manual
    assert manual[0]["reason"] == "incomplete_history"


def test_hdfc_ledger_without_ecas_line_flags_merged_out(make_investor):
    """Pre-merger HDFC on the tradebook with only HDFCBANK on eCAS → manual flag."""
    inv = make_investor()
    hdfc_isin = "INE001A01036"
    row = f"equity,HDFC Ltd,HDFC,{hdfc_isin},2022-06-27,buy,50,2200,{_DEMAT},ZERODHA\n"
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")

    hdfcbank = CoreSecurity(
        type=SecurityType.EQUITY,
        name="HDFC Bank Ltd",
        isin="INE040A01034",
        symbol="HDFCBANK",
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
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=hdfc_sec)
    manual = [i for i in status.issues if i.get("type") == "corporate_action_manual"]
    assert manual
    assert manual[0]["reason"] == "ledger_position_not_in_holdings"
