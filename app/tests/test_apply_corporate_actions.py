"""Apply corporate-action events to investor ledgers."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import (
    AppliedCorporateAction,
    CorporateActionReference,
    Folio,
    ImportJob,
    Security,
    SecurityIntegrityStatus,
)
from folioman_app.models.jobs import ImportKind
from folioman_app.services.corporate_actions import (
    apply_corporate_actions_to_folio,
    apply_suggested_corporate_actions,
)
from folioman_app.services.projected_ledger import compute_ledger
from folioman_app.tasks.import_csv import process_csv
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.corporate_actions import CorporateActionApplyEvent
from folioman_core.fifo import apply_fifo, net_units_from_transactions
from folioman_core.models import SecurityType, TransactionType
from folioman_core.models import Transaction as CoreTransaction
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


def test_apply_suggested_accepts_fk_linked_ref_under_a_prior_isin(make_investor):
    """A face-value split is cached under the pre-split ISIN but FK-linked to the
    current security. Applying via the suggestion path must trust the FK, not reject
    on the drifted ISIN ('reference does not match this security')."""
    inv = make_investor()
    _import_allcargo(inv)
    persist_ecas_statement(inv, _ecas_allcargo("960"), source_ref="ecas1")
    sec = Security.objects.get(isin=_ALLCARGO)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    ref = CorporateActionReference.objects.create(
        security=sec,
        isin="INE418H01018",  # a prior ISIN, differs from sec.isin
        symbol="ALLCARGO",
        exchange="NSE",
        ex_date=dt.date(2024, 1, 2),
        subject="Bonus 3:1",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=Decimal("4"),
        needs_review=False,
        source="NSE",
    )
    summary = apply_suggested_corporate_actions(inv, folio, sec, [ref.id])
    assert summary["events_applied"] == 1
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio=folio)
    assert status.status == "reconciled"


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
    # The bonus is recorded once in the event log; the trade rows are never rewritten.
    assert (
        AppliedCorporateAction.objects.filter(investor=inv, folio=folio, kind="bonus").count() == 1
    )
    assert not inv.transactions.filter(folio=folio, transaction_type="bonus").exists()


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
    # The as-traded 2014 buy stays exactly as imported (never rewritten), but is flagged
    # cost-basis incomplete (pre-window, unreliable) — so the merger projection, which
    # only feeds complete rows to FIFO, carries no cost basis onto the acquirer.
    old_sec = Security.objects.get(isin=old_isin)
    old_buy = inv.transactions.get(security=old_sec, folio=folio, transaction_type="buy")
    assert old_buy.date == dt.date(2014, 6, 1)
    assert old_buy.cost_basis_complete is False
    new_sec = Security.objects.get(isin=new_isin)
    assert compute_ledger(inv, new_sec, folio=folio) == []


def test_merger_persists_exact_cost_total_for_indivisible_ratio(make_investor):
    """An indivisible merger ratio's exact lot cost survives the DB round-trip.

    1 old -> 3 new makes the new per-unit 1000/3 (repeating), which rounds to the
    column's 6dp on save. The preserved cost_total keeps the realised gain exact
    when valuation/tax replay the persisted rows.
    """
    inv = make_investor()
    old_isin = "INE001A01036"
    new_isin = "INE040A01034"
    row = f"equity,Old Co,OLDCO,{old_isin},2020-06-01,buy,30,1000,{_DEMAT},ZERODHA\n"
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")

    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    new_sec_core = CoreSecurity(
        type=SecurityType.EQUITY, name="New Co", isin=new_isin, symbol="NEWCO"
    )
    events = [
        CorporateActionApplyEvent(
            kind=CorpActionType.MERGER,
            ex_date=dt.date(2021, 7, 1),
            security=new_sec_core,
            merger_old_security=CoreSecurity(
                type=SecurityType.EQUITY, name="Old Co", isin=old_isin, symbol="OLDCO"
            ),
            merger_new_security=new_sec_core,
            merger_ratio=Decimal("3"),
            source_ref="merger:indivisible",
        )
    ]
    apply_corporate_actions_to_folio(inv, folio, events=events)

    # The projection rebases the 30-unit lot onto the acquirer at 3:1, preserving the
    # exact lot cost (the per-unit 1000/3 repeats, so only the total is exact).
    new_sec = Security.objects.get(isin=new_isin)
    rows = compute_ledger(inv, new_sec, folio=folio)
    buy = next(r for r in rows if r.type is TransactionType.BUY)
    assert buy.units == Decimal("90")
    assert buy.cost_total == Decimal("30000")  # exact total preserved through projection

    # Sell the whole projected position: the gain is exact (45000 - 30000), no 6dp drift.
    sell = CoreTransaction(
        security=buy.security,
        date=dt.date(2022, 1, 1),
        type=TransactionType.SELL,
        units=Decimal("90"),
        nav_or_price=Decimal("500"),
        source=buy.source,
    )
    assert apply_fifo([*rows, sell]).pnl == Decimal("15000")


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
    # The as-traded HDFC buy stays exactly as imported (immutable ledger); the projection
    # rebases it onto HDFCBANK and applies the bonus. 50 * 42/25 = 84, doubled = 168.
    assert inv.transactions.filter(security=hdfc_sec, folio=folio).count() == 1
    assert not inv.transactions.filter(security=hdfcbank_sec, folio=folio).exists()
    assert net_units_from_transactions(compute_ledger(inv, hdfc_sec, folio=folio)) == Decimal("0")
    assert net_units_from_transactions(compute_ledger(inv, hdfcbank_sec, folio=folio)) == Decimal(
        "168"
    )


def test_fractional_entitlement_settled_against_ecas_whole_holding(make_investor):
    """A merger fraction over the whole eCAS holding is booked as a zero-gain SELL.

    11 sh rebased at 3:2 (ratio 1.5) -> 16.5; the demat holds 16 whole shares (the
    registrar paid cash for the 0.5). The 0.5 is settled at cost so the ledger
    reconciles to 16, the row is tagged + idempotent.
    """
    inv = make_investor()
    old_isin = "INE001A01036"
    new_isin = "INE040A01034"
    row = f"equity,Old Co,OLDCO,{old_isin},2020-06-01,buy,11,100,{_DEMAT},ZERODHA\n"
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")

    newco = CoreSecurity(type=SecurityType.EQUITY, name="New Co", isin=new_isin, symbol="NEWCO")
    persist_ecas_statement(
        inv,
        EcasStatement(
            depository=Depository.CDSL,
            statement_date=dt.date(2025, 6, 1),
            accounts=[
                EcasAccountBlock(
                    folio=CoreFolio(folio_type="demat", number=_DEMAT, broker="ZERODHA"),
                    holdings=[EcasHoldingLine(security=newco, units="16", value_observed="32000")],
                )
            ],
        ),
        source_ref="ecas-newco",
    )

    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    events = [
        CorporateActionApplyEvent(
            kind=CorpActionType.MERGER,
            ex_date=dt.date(2021, 7, 1),
            security=newco,
            merger_old_security=CoreSecurity(
                type=SecurityType.EQUITY, name="Old Co", isin=old_isin, symbol="OLDCO"
            ),
            merger_new_security=newco,
            merger_ratio=Decimal("1.5"),
            source_ref="merger:frac",
        )
    ]
    apply_corporate_actions_to_folio(inv, folio, events=events)

    new_sec = Security.objects.get(isin=new_isin)
    frac = inv.transactions.get(
        folio=folio, security=new_sec, source_ref=f"fractional-entitlement:{new_isin}"
    )
    assert frac.transaction_type == "sell"
    assert frac.units == Decimal("0.5")
    assert frac.source == "corporate-action"  # tagged for a future manual override

    status = SecurityIntegrityStatus.objects.get(investor=inv, security=new_sec, folio=folio)
    assert status.status == "reconciled"
    assert status.units_from_transactions == Decimal("16")
    assert status.units_from_holdings == Decimal("16")

    # Idempotent: re-applying books no second settlement row.
    apply_corporate_actions_to_folio(inv, folio, events=events)
    assert (
        inv.transactions.filter(
            folio=folio, security=new_sec, source_ref=f"fractional-entitlement:{new_isin}"
        ).count()
        == 1
    )


def test_dividend_reference_is_not_auto_applicable(make_investor):
    """Only bonus/split apply from a cached feed reference. A dividend reference must
    be rejected — applying it here would double-write against dividend attribution."""
    inv = make_investor()
    _import_allcargo(inv)
    sec = Security.objects.get(isin=_ALLCARGO)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    ref = CorporateActionReference.objects.create(
        security=sec,
        isin=_ALLCARGO,
        symbol="ALLCARGO",
        exchange="NSE",
        ex_date=dt.date(2024, 1, 2),
        subject="Dividend - Rs 5 Per Share",
        parsed_type=CorpActionType.DIVIDEND.value,
        amount=Decimal("5"),
        needs_review=False,
        source="NSE",
    )
    with pytest.raises(ValueError, match="not auto-applicable"):
        apply_corporate_actions_to_folio(inv, folio, reference_ids=[ref.id])


def test_manual_demerger_authoring_is_rejected(make_investor):
    """Demerger is not a manually-authored corporate action: it's resolved by
    recording the child's lots (opening lots) + linking the parent, not via the
    apply engine."""
    from folioman_app.services.corporate_actions import apply_manual_corporate_action

    inv = make_investor()
    _import_allcargo(inv)
    sec = Security.objects.get(isin=_ALLCARGO)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    with pytest.raises(ValueError, match="not supported for manual authoring"):
        apply_manual_corporate_action(
            inv,
            folio,
            sec,
            kind="demerger",
            ex_date=dt.date(2024, 1, 2),
            counterparty_isin="INE999X01010",
        )
