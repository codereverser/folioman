"""eCAS import service: multi-account holdings, snapshot_only, reconcile.

Committed tests use synthetic EcasStatements (real ISINs, fake identity).
Real-PDF parsing is exercised by the local skipif smoke in
test_import_ecas_smoke.py.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from folioman_app.models import (
    Folio,
    Holding,
    ImportJob,
    Security,
    SecurityIntegrityStatus,
    Transaction,
)
from folioman_app.models.jobs import ImportKind
from folioman_app.tasks.import_cas import persist_mf_statement
from folioman_app.tasks.import_csv import create_manual_transaction, process_csv
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_core.models import SecurityType, TransactionType
from folioman_core.models.cas import (
    Depository,
    EcasAccountBlock,
    EcasHoldingLine,
    EcasStatement,
    MfCasLineItem,
    MfCasSchemeBlock,
    MfCasStatement,
)
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.security import Security as CoreSecurity

pytestmark = pytest.mark.django_db

_FUND_ISIN = "INF879O01027"


def _cdsl_statement() -> EcasStatement:
    reliance = CoreSecurity(
        type=SecurityType.EQUITY, name="Reliance Industries", isin="INE002A01018"
    )
    bond = CoreSecurity(type=SecurityType.BOND, name="REC Ltd Bond", isin="INE020B08DG9")
    account1 = EcasAccountBlock(
        folio=CoreFolio(folio_type="demat", number="1208160001234567", broker="ZERODHA"),
        holdings=[EcasHoldingLine(security=reliance, units="10", value_observed="28500")],
    )
    account2 = EcasAccountBlock(
        folio=CoreFolio(folio_type="demat", number="IN30021412345678", broker="HDFC SECURITIES"),
        holdings=[EcasHoldingLine(security=bond, units="5", value_observed="50000")],
    )
    return EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        investor_name="Test Investor",
        pan_masked="XXXXX1234X",
        accounts=[account1, account2],
    )


def _mf_buy(isin: str, units: str) -> MfCasStatement:
    security = CoreSecurity(
        type=SecurityType.MF, name="Parag Parikh Flexi Cap", amfi_code="122639", isin=isin
    )
    block = MfCasSchemeBlock(
        folio=CoreFolio(folio_type="mf", number="999/0", amc_code="PPFAS"),
        security=security,
        transactions=[
            MfCasLineItem(
                date=dt.date(2024, 1, 1),
                transaction_type=TransactionType.BUY,
                units=units,
                nav="75",
            )
        ],
    )
    return MfCasStatement(schemes=[block])


def _ecas_demat_mf(isin: str, units: str) -> EcasStatement:
    security = CoreSecurity(type=SecurityType.MF, name="Parag Parikh Flexi Cap", isin=isin)
    account = EcasAccountBlock(
        folio=CoreFolio(folio_type="demat", number="1208160009999999", broker="ZERODHA"),
        holdings=[EcasHoldingLine(security=security, units=units, value_observed="3750")],
    )
    return EcasStatement(
        depository=Depository.CDSL, statement_date=dt.date(2025, 6, 1), accounts=[account]
    )


def test_account_count_excludes_mutual_fund_folios_section(make_investor):
    """A CDSL eCAS with 2 demat accounts + the RTA "Mutual Fund Folios" section
    must report 2 demat accounts and 1 MF folio — the synthetic MF block is not
    a demat account."""
    fund = CoreSecurity(type=SecurityType.MF, name="Parag Parikh Flexi Cap", isin=_FUND_ISIN)
    mf_section = EcasAccountBlock(
        folio=CoreFolio(folio_type="demat", number="Mutual Fund Folios", broker="UNKNOWN"),
        kind="mf_folios",
        holdings=[
            EcasHoldingLine(
                security=fund,
                units="50",
                value_observed="6000",
                folio=CoreFolio(folio_type="mf", number="999/0"),
            )
        ],
    )
    statement = _cdsl_statement()
    statement.accounts.append(mf_section)

    inv = make_investor()
    summary = persist_ecas_statement(inv, statement, source_ref="ecas1")

    assert summary["accounts"] == 2
    assert summary["mf_folios"] == 1
    assert summary["holdings_created"] == 3  # MF holding still lands, under its RTA folio


def test_ecas_never_clobbers_clean_scheme_name(make_investor):
    """CDSL prints MF schemes with an internal code prefix ("001ZG - Parag
    Parikh…"). An eCAS imported after the MF CAS must not replace the clean RTA
    name; an eCAS-first fund keeps its garbled name until an MF CAS refines it."""
    inv = make_investor()
    persist_mf_statement(inv, _mf_buy(_FUND_ISIN, "50"))
    clean = Security.objects.get(isin=_FUND_ISIN).name

    garbled = CoreSecurity(
        type=SecurityType.MF, name="001ZG - Parag Parikh Flexi Cap Direct Gr", isin=_FUND_ISIN
    )
    statement = EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number="1208160001234567", broker="ZERODHA"),
                holdings=[EcasHoldingLine(security=garbled, units="50", value_observed="6000")],
            )
        ],
    )
    persist_ecas_statement(inv, statement, source_ref="ecas1", confirm=True)

    assert Security.objects.get(isin=_FUND_ISIN).name == clean

    # The reverse direction still refines: an MF CAS replaces the garbled name.
    Security.objects.filter(isin=_FUND_ISIN).update(name="001ZG - Parag Parikh Flexi Cap")
    persist_mf_statement(inv, _mf_buy(_FUND_ISIN, "10"))
    assert Security.objects.get(isin=_FUND_ISIN).name == clean


def test_persist_creates_folios_and_holdings(make_investor):
    inv = make_investor()
    summary = persist_ecas_statement(inv, _cdsl_statement(), source_ref="ecas1")
    assert summary["accounts"] == 2
    assert summary["holdings_created"] == 2
    assert summary["securities"] == 2
    assert Folio.objects.filter(investor=inv, folio_type="demat").count() == 2
    assert Holding.objects.filter(investor=inv).count() == 2
    # holdings land under the right account folios
    reliance = Security.objects.get(isin="INE002A01018")
    holding = Holding.objects.get(investor=inv, security=reliance)
    assert holding.folio.broker == "ZERODHA"
    assert holding.units == Decimal("10")


def test_ecas_only_security_is_snapshot_only(make_investor):
    inv = make_investor()
    persist_ecas_statement(inv, _cdsl_statement(), source_ref="ecas1")
    reliance = Security.objects.get(isin="INE002A01018")
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=reliance)
    # Holdings but no transactions -> snapshot only, not tax-safe (no cost history).
    assert status.status == "snapshot_only"
    assert status.tax_safe is False


def test_reimport_same_statement_replaces_without_duplicating(make_investor):
    inv = make_investor()
    persist_ecas_statement(inv, _cdsl_statement(), source_ref="ecas1")
    # Same date, same securities -> no removals, no confirm needed; the prior
    # snapshot is replaced (delete + re-create), never duplicated.
    summary = persist_ecas_statement(inv, _cdsl_statement(), source_ref="ecas2")
    assert summary["holdings_removed"] == 0
    assert summary["holdings_created"] == 2
    assert Holding.objects.filter(investor=inv).count() == 2  # not duplicated


def test_ecas_removal_requires_confirmation_and_persists_nothing(make_investor):
    inv = make_investor()
    # First statement: two demat holdings (Reliance, REC bond).
    persist_ecas_statement(inv, _cdsl_statement(), source_ref="e1")
    assert Holding.objects.filter(investor=inv).count() == 2

    # A newer statement that drops the bond (sold). Without confirm: previewed,
    # nothing changed.
    reliance_only = EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 9, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number="1208160001234567", broker="ZERODHA"),
                holdings=[
                    EcasHoldingLine(
                        security=CoreSecurity(
                            type=SecurityType.EQUITY,
                            name="Reliance Industries",
                            isin="INE002A01018",
                        ),
                        units="10",
                        value_observed="30000",
                    )
                ],
            )
        ],
    )
    summary = persist_ecas_statement(inv, reliance_only, source_ref="e2")
    assert summary["requires_confirmation"] is True
    assert [r["isin"] for r in summary["removals"]] == ["INE020B08DG9"]  # the bond
    # Nothing persisted — both holdings still present.
    assert Holding.objects.filter(investor=inv).count() == 2

    # With confirm: the bond is removed; only Reliance remains, no ghost.
    summary = persist_ecas_statement(inv, reliance_only, source_ref="e2", confirm=True)
    assert summary["holdings_removed"] == 1
    assert summary["removed"][0]["isin"] == "INE020B08DG9"
    assert list(Holding.objects.filter(investor=inv).values_list("security__isin", flat=True)) == [
        "INE002A01018"
    ]
    # The removed bond's integrity status is cleared (no stale snapshot_only).
    bond = Security.objects.get(isin="INE020B08DG9")
    assert not SecurityIntegrityStatus.objects.filter(investor=inv, security=bond).exists()


def test_ecas_older_than_latest_is_rejected(make_investor):
    from folioman_app.tasks.import_ecas import StaleStatementError

    inv = make_investor()
    persist_ecas_statement(inv, _cdsl_statement(), source_ref="jun")  # dated 2025-06-01
    older = EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 1, 1),
        accounts=_cdsl_statement().accounts,
    )
    with pytest.raises(StaleStatementError):
        persist_ecas_statement(inv, older, source_ref="jan")
    assert Holding.objects.filter(investor=inv).count() == 2  # unchanged


def test_mf_cas_and_ecas_demat_are_independent_per_folio(make_investor):
    # A fund held in an MF folio (CAS) and the SAME fund in a demat account (eCAS)
    # are different folios/accounts — integrity is per-folio, so they are tracked
    # independently and never cross-reconciled into a spurious mismatch.
    inv = make_investor()
    persist_mf_statement(inv, _mf_buy(_FUND_ISIN, "60"), source_ref="cas")
    persist_ecas_statement(inv, _ecas_demat_mf(_FUND_ISIN, "50"), source_ref="ecas")
    sec = Security.objects.get(isin=_FUND_ISIN)
    by_type = {
        s.folio.folio_type: (s.status, s.tax_safe)
        for s in SecurityIntegrityStatus.objects.filter(investor=inv, security=sec)
    }
    assert by_type == {"mf": ("full_history", True), "demat": ("snapshot_only", False)}


def test_ledger_and_ecas_in_same_folio_reconcile(make_investor):
    # The demat trust check: a ledger and an eCAS holding in the SAME demat
    # account (same folio number) reconcile per-folio.
    inv = make_investor()
    demat = "1208160007654321"
    equity_isin = "INE002A01018"
    create_manual_transaction(
        inv,
        {
            "security_type": "equity",
            "name": "Reliance Industries",
            "isin": equity_isin,
            "folio_number": demat,
            "broker": "ZERODHA",
            "date": dt.date(2024, 1, 1),
            "transaction_type": "buy",
            "units": Decimal("60"),
            "price": Decimal("2000"),
        },
    )
    eq = CoreSecurity(type=SecurityType.EQUITY, name="Reliance Industries", isin=equity_isin)
    ecas = EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number=demat, broker="ZERODHA"),
                holdings=[EcasHoldingLine(security=eq, units="60", value_observed="120000")],
            )
        ],
    )
    persist_ecas_statement(inv, ecas, source_ref="ecas")
    sec = Security.objects.get(isin=equity_isin)
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio__number=demat)
    assert status.status == "reconciled"
    assert status.tax_safe is True


# --- out-of-order eCAS: re-point a dangling tradebook folio -------------------

_RELIANCE = "INE002A01018"
_TRADEBOOK_HEADER = (
    "security_type,name,symbol,isin,date,transaction_type,units,price,folio_number,broker\n"
)


def _import_tradebook(inv, demat_number: str, *, units="10", isin=_RELIANCE):
    """Import a one-row equity tradebook for `inv` onto `demat_number`."""
    row = (
        f"equity,Reliance Industries,RELIANCE,{isin},2024-01-15,buy,{units},2800,"
        f"{demat_number},ZERODHA\n"
    )
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    return process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")


def _ecas_reliance(demat_number: str, units="10") -> EcasStatement:
    reliance = CoreSecurity(type=SecurityType.EQUITY, name="Reliance Industries", isin=_RELIANCE)
    return EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        investor_name="Test Investor",
        pan_masked="XXXXX1234X",
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number=demat_number, broker="ZERODHA"),
                holdings=[EcasHoldingLine(security=reliance, units=units, value_observed="28500")],
            )
        ],
    )


def test_ecas_repoints_dangling_tradebook_folio(make_investor):
    """A tradebook imported onto a mistyped/unknown demat number is re-pointed onto
    the real demat folio when an overlapping eCAS arrives, then reconciles."""
    inv = make_investor()
    typo = "1208169999999999"  # valid shape, wrong account
    _import_tradebook(inv, typo)
    assert Folio.objects.filter(investor=inv, number=typo).exists()

    real = "1208160001234567"
    persist_ecas_statement(inv, _ecas_reliance(real), source_ref="e1")

    sec = Security.objects.get(isin=_RELIANCE)
    # The dangling folio is gone; the ledger now lives on the real demat folio.
    assert not Folio.objects.filter(investor=inv, number=typo).exists()
    txn = Transaction.objects.get(investor=inv, security=sec)
    assert txn.folio.number == real
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio__number=real)
    assert status.status == "reconciled"


def test_ecas_direct_match_needs_no_repoint(make_investor):
    """When the tradebook already carries the real BO ID, the later eCAS matches it
    by number — one folio, reconciled, nothing re-pointed."""
    inv = make_investor()
    real = "1208160001234567"
    _import_tradebook(inv, real)
    persist_ecas_statement(inv, _ecas_reliance(real), source_ref="e1")

    assert Folio.objects.filter(investor=inv, folio_type="demat").count() == 1
    sec = Security.objects.get(isin=_RELIANCE)
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio__number=real)
    assert status.status == "reconciled"


def test_tradebook_net_below_ecas_holding_is_acknowledgeable_mismatch(make_investor):
    """A complete tradebook whose net units fall short of the eCAS holding (missing
    buys) reconciles as a flagged mismatch, not silent corruption."""
    inv = make_investor()
    real = "1208160001234567"
    _import_tradebook(inv, real, units="6")  # ledger net 6
    persist_ecas_statement(inv, _ecas_reliance(real, units="10"), source_ref="e1")  # holding 10

    sec = Security.objects.get(isin=_RELIANCE)
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio__number=real)
    assert status.status == "mismatch"
    assert status.tax_safe is False
    assert status.units_from_transactions == Decimal("6")
    assert status.units_from_holdings == Decimal("10")


def test_ecas_ambiguous_overlap_prompts_not_auto(make_investor):
    """A dangling folio overlapping two eCAS demat accounts is not auto-merged — a
    folio-link suggestion is raised for the user instead."""
    inv = make_investor()
    typo = "1208169999999999"
    _import_tradebook(inv, typo)

    reliance = CoreSecurity(type=SecurityType.EQUITY, name="Reliance Industries", isin=_RELIANCE)
    two_accounts = EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        investor_name="Test Investor",
        pan_masked="XXXXX1234X",
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number="1208160001234567", broker="ZERODHA"),
                holdings=[EcasHoldingLine(security=reliance, units="6", value_observed="17000")],
            ),
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number="1208160007654321", broker="ZERODHA"),
                holdings=[EcasHoldingLine(security=reliance, units="4", value_observed="11500")],
            ),
        ],
    )
    summary = persist_ecas_statement(inv, two_accounts, source_ref="e1")

    # Not auto-merged: the dangling folio survives, and a link suggestion is recorded.
    assert Folio.objects.filter(investor=inv, number=typo).exists()
    suggestions = [q for q in summary.get("quarantined", []) if q.get("kind") == "folio_link"]
    assert len(suggestions) == 1
    assert set(suggestions[0]["raw"]["candidate_folios"]) == {
        "1208160001234567",
        "1208160007654321",
    }


def test_import_via_api_end_to_end(client, patch_cas, make_parsed_cas):
    # The unified /imports/cas endpoint auto-detects an eCAS and routes it to
    # holdings persistence, with a notice that it's a net-worth snapshot. The
    # investor is resolved/created from the statement's (primary owner) PAN.
    patch_cas(make_parsed_cas(ecas=_cdsl_statement()))
    upload = SimpleUploadedFile("ecas.pdf", b"%PDF fake")
    resp = client.post("/api/imports/cas", {"file": upload, "password": "x"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "success"
    assert body["result"]["detected"] == "ecas"
    assert body["result"]["notice"]
    assert body["result"]["holdings_created"] == 2
    assert Holding.objects.filter(investor_id=body["investor_id"]).count() == 2
