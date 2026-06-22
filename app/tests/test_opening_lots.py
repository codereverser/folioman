"""Opening lots and identity remap for eCAS-only equities."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import (
    AppliedCorporateAction,
    Folio,
    Security,
    SecurityIntegrityStatus,
    Transaction,
)
from folioman_app.models.jobs import ImportJob, ImportKind
from folioman_app.services.identity_remap import apply_identity_remap
from folioman_app.services.opening_lots import (
    record_opening_lot,
    record_opening_lots,
    remove_opening_lots,
)
from folioman_app.services.tax_export import build_capital_gains
from folioman_app.tasks.import_csv import create_manual_transaction, process_csv
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_core.models import SecurityType, TransactionSource, TransactionType
from folioman_core.models.cas import Depository, EcasAccountBlock, EcasHoldingLine, EcasStatement
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.security import Security as CoreSecurity
from folioman_core.opening_lot import OpeningLotKind

pytestmark = pytest.mark.django_db

_DEMAT = "1208160001234567"
_OLD_ISIN = "INE418H01026"
_NEW_ISIN = "INE418H01034"


def _ecas_hdbfs(units: str = "50") -> EcasStatement:
    from folioman_core.models import SecurityType

    security = CoreSecurity(type=SecurityType.EQUITY, name="HDB Financial Services", isin=_OLD_ISIN)
    return EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number=_DEMAT, broker="ZERODHA"),
                holdings=[EcasHoldingLine(security=security, units=units)],
            )
        ],
    )


def test_ecas_only_equity_flags_opening_lot_needed(make_investor):
    inv = make_investor()
    persist_ecas_statement(inv, _ecas_hdbfs(), source_ref="ecas1")
    sec = Security.objects.get(isin=_OLD_ISIN)
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec)
    assert status.status == "snapshot_only"
    kinds = [i["type"] for i in status.issues]
    assert "opening_lot_needed" in kinds


def test_record_opening_lot_reconciles(make_investor):
    inv = make_investor()
    persist_ecas_statement(inv, _ecas_hdbfs(), source_ref="ecas1")
    sec = Security.objects.get(isin=_OLD_ISIN)
    folio = sec.holdings.get(investor=inv).folio
    summary = record_opening_lot(
        inv,
        folio,
        sec,
        kind=OpeningLotKind.IPO_ALLOTMENT,
        lot_date=dt.date(2024, 5, 1),
        price=Decimal("700"),
    )
    assert summary["created"] == 1
    assert summary["cost_basis_complete"] is True
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio=folio)
    assert status.status == "reconciled"
    assert not any(i["type"] == "opening_lot_needed" for i in status.issues)


def test_record_opening_lot_unknown_cost_basis(make_investor):
    inv = make_investor()
    persist_ecas_statement(inv, _ecas_hdbfs(), source_ref="ecas1")
    sec = Security.objects.get(isin=_OLD_ISIN)
    folio = sec.holdings.get(investor=inv).folio
    record_opening_lot(
        inv,
        folio,
        sec,
        kind=OpeningLotKind.TRANSFER_IN,
        lot_date=dt.date(2023, 1, 1),
        cost_basis_unknown=True,
    )
    txn = Transaction.objects.get(investor=inv, security=sec)
    assert txn.cost_basis_complete is False
    assert txn.nav_or_price == Decimal("0")


def test_apply_identity_remap_moves_ledger(make_investor):
    inv = make_investor()
    create_manual_transaction(
        inv,
        {
            "security_type": "equity",
            "name": "Old Co",
            "symbol": "OLDCO",
            "isin": _OLD_ISIN,
            "folio_number": _DEMAT,
            "broker": "ZERODHA",
            "date": "2024-01-01",
            "transaction_type": "buy",
            "units": "10",
            "price": "100",
        },
    )
    old = Security.objects.get(isin=_OLD_ISIN)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    summary = apply_identity_remap(inv, folio, old, to_isin=_NEW_ISIN, to_name="New Co")
    assert summary["transactions_updated"] == 1
    assert Transaction.objects.filter(investor=inv, security__isin=_NEW_ISIN).count() == 1
    assert not Transaction.objects.filter(investor=inv, security__isin=_OLD_ISIN).exists()


# --- multi-lot opening lots (demerger child, incl. one already sold) ---------

_CHILD_ISIN = "INE0O3901029"


def test_multi_lot_opening_resolves_a_sold_off_demerger_child(
    make_investor, make_security, make_folio
):
    inv = make_investor()
    sec = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="CHILD", name="Child Co"
    )
    folio = make_folio(investor=inv, folio_type="demat", number="1208160000000001")
    # A demerger child received earlier and fully sold in FY2023-24 — only orphan sells
    # exist (no matching buy), flagged cost-basis-incomplete at import.
    Transaction.objects.create(
        investor=inv,
        security=sec,
        folio=folio,
        date=dt.date(2024, 2, 2),
        transaction_type=TransactionType.SELL.value,
        units=Decimal("240"),
        nav_or_price=Decimal("55"),
        source=TransactionSource.CSV_IMPORT.value,
        source_ref="t-sell",
        cost_basis_complete=False,
    )

    result = record_opening_lots(
        inv,
        folio,
        sec,
        kind=OpeningLotKind.DEMERGER_RESULT,
        lots=[
            {"lot_date": dt.date(2018, 10, 9), "units": Decimal("40"), "price": Decimal("45")},
            {"lot_date": dt.date(2019, 2, 12), "units": Decimal("200"), "price": Decimal("48")},
        ],
    )
    assert result["created"] == 2
    assert result["net_units"] == "0E-8"  # 240 received - 240 sold

    # The receipt lots made FIFO solvent → the orphan sell is now complete and taxed.
    sell = Transaction.objects.get(security=sec, transaction_type="sell")
    assert sell.cost_basis_complete is True

    cg = build_capital_gains(inv, "2023-24", include_unreconciled=True)
    rows = [r for r in cg["rows"] if r["isin"] == _CHILD_ISIN]
    # cost = 40*45 + 200*48 = 11,400; proceeds 240*55 = 13,200; long-term gain 1,800.
    assert sum(r["gain"] for r in rows) == Decimal("1800.00")
    assert all(r["term"] == "long" for r in rows)


def test_multi_lot_opening_is_idempotent(make_investor, make_security, make_folio):
    inv = make_investor()
    sec = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="CHILD", name="Child Co"
    )
    folio = make_folio(investor=inv, folio_type="demat", number="1208160000000002")
    lot = [{"lot_date": dt.date(2019, 1, 1), "units": Decimal("10"), "price": Decimal("100")}]
    record_opening_lots(inv, folio, sec, kind=OpeningLotKind.DEMERGER_RESULT, lots=lot)
    with pytest.raises(ValueError, match="already recorded"):
        record_opening_lots(inv, folio, sec, kind=OpeningLotKind.DEMERGER_RESULT, lots=lot)


# --- a tradebook supersedes a manual opening-lot placeholder -----------------

_TRADEBOOK_HEADER = (
    "security_type,name,symbol,isin,date,transaction_type,units,price,folio_number,broker\n"
)


def test_tradebook_import_supersedes_a_manual_opening_lot(make_investor):
    """An eCAS-only holding gets an opening lot; later the real tradebook arrives. The
    placeholder is dropped so its units don't double-count — the holding still ties out."""
    inv = make_investor()
    persist_ecas_statement(inv, _ecas_hdbfs("50"), source_ref="ecas1")
    sec = Security.objects.get(isin=_OLD_ISIN)
    folio = sec.holdings.get(investor=inv).folio
    record_opening_lot(
        inv,
        folio,
        sec,
        kind=OpeningLotKind.IPO_ALLOTMENT,
        lot_date=dt.date(2024, 5, 1),
        price=Decimal("700"),
    )
    assert inv.transactions.filter(security=sec, source_ref__startswith="opening-lot:").exists()

    # The real tradebook for the same holding (50 bought) arrives.
    row = f"equity,HDB Financial,HDB,{_OLD_ISIN},2024-05-01,buy,50,700,{_DEMAT},ZERODHA\n"
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    result = process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")

    # Placeholder superseded; only the real buy remains → no double count.
    assert not inv.transactions.filter(security=sec, source_ref__startswith="opening-lot:").exists()
    assert (
        inv.transactions.filter(
            security=sec, source=TransactionSource.CSV_IMPORT.value, transaction_type="buy"
        ).count()
        == 1
    )
    assert result.get("superseded_opening_lots")
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio=folio)
    assert status.status == "reconciled"  # 50 ledger == 50 eCAS, not 100


def test_tradebook_import_keeps_a_demerger_childs_receipt_lots(
    make_investor, make_security, make_folio
):
    """A demerger child's receipt lots are not missing-history placeholders — a later
    buy for the child must not supersede them (they carry a parent cost-reduction link)."""
    inv = make_investor()
    parent = make_security(
        security_type=SecurityType.EQUITY.value, isin="INE418H01029", symbol="PARENT", name="Parent"
    )
    child = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="CHILD", name="Child"
    )
    folio = make_folio(investor=inv, folio_type="demat", number="1208160000000003")
    record_opening_lots(
        inv,
        folio,
        child,
        kind=OpeningLotKind.DEMERGER_RESULT,
        lots=[{"lot_date": dt.date(2019, 2, 12), "units": Decimal("40"), "price": Decimal("7")}],
    )
    # A demerger link makes the child a non-placeholder (parent is the counterparty).
    AppliedCorporateAction.objects.create(
        investor=inv,
        folio=folio,
        security=parent,
        counterparty_security=child,
        kind="demerger",
        ex_date=dt.date(2022, 6, 1),
        source_ref=f"demerger:{child.id}",
        params={"reductions": {"2019-02-12": "120.00"}},
    )

    row = f"equity,Child,CHILD,{_CHILD_ISIN},2023-01-01,buy,5,60,1208160000000003,ZERODHA\n"
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")

    # Receipt lots survive — only a placeholder (not a demerger receipt) is superseded.
    assert inv.transactions.filter(security=child, source_ref__startswith="opening-lot:").exists()


# --- removing a recorded opening lot -----------------------------------------


def test_recorded_opening_lot_is_marked_and_removable(make_investor):
    """A recorded opening lot is flagged ``opening_lot_recorded`` (so the UI can offer to
    remove it), and removing it returns the holding to needing one."""
    inv = make_investor()
    persist_ecas_statement(inv, _ecas_hdbfs("50"), source_ref="ecas1")
    sec = Security.objects.get(isin=_OLD_ISIN)
    folio = sec.holdings.get(investor=inv).folio
    record_opening_lot(
        inv,
        folio,
        sec,
        kind=OpeningLotKind.IPO_ALLOTMENT,
        lot_date=dt.date(2024, 5, 1),
        price=Decimal("700"),
    )
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio=folio)
    assert status.status == "reconciled"
    assert any(i["type"] == "opening_lot_recorded" for i in status.issues)

    out = remove_opening_lots(inv, folio, sec)
    assert out["removed"] == 1
    assert not inv.transactions.filter(security=sec, source_ref__startswith="opening-lot:").exists()
    status.refresh_from_db()
    # Back to snapshot-only, prompting for a lot again — the holding still exists in eCAS.
    assert status.status == "snapshot_only"
    assert any(i["type"] == "opening_lot_needed" for i in status.issues)


def test_remove_demerger_receipt_also_drops_the_parent_link(
    make_investor, make_security, make_folio
):
    """Removing a demerger child's receipt lots also removes the parent cost-reduction
    link, so the parent's basis is no longer reduced for shares that are gone."""
    inv = make_investor()
    parent = make_security(
        security_type=SecurityType.EQUITY.value, isin="INE418H01029", symbol="PARENT", name="Parent"
    )
    child = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="CHILD", name="Child"
    )
    folio = make_folio(investor=inv, folio_type="demat", number="1208160000000004")
    # Parent holds the lot the child inherited (so the demerger auto-matches).
    Transaction.objects.create(
        investor=inv,
        security=parent,
        folio=folio,
        date=dt.date(2019, 2, 12),
        transaction_type=TransactionType.BUY.value,
        units=Decimal("40"),
        nav_or_price=Decimal("103"),
        source=TransactionSource.CSV_IMPORT.value,
        source_ref="p1",
        cost_basis_complete=True,
    )
    record_opening_lots(
        inv,
        folio,
        child,
        kind=OpeningLotKind.DEMERGER_RESULT,
        lots=[{"lot_date": dt.date(2019, 2, 12), "units": Decimal("40"), "price": Decimal("7")}],
        demerger_date=dt.date(2022, 6, 1),
    )
    assert AppliedCorporateAction.objects.filter(
        investor=inv, security=parent, kind="demerger"
    ).exists()

    out = remove_opening_lots(inv, folio, child)
    assert out["removed"] == 1
    assert parent.id in out["parent_ids"]
    # The link is gone — the parent stops shedding cost for the removed receipt.
    assert not AppliedCorporateAction.objects.filter(investor=inv, kind="demerger").exists()


def test_remove_opening_lot_errors_when_none_present(make_investor, make_security, make_folio):
    inv = make_investor()
    sec = make_security(
        security_type=SecurityType.EQUITY.value, isin=_OLD_ISIN, symbol="X", name="X"
    )
    folio = make_folio(investor=inv, folio_type="demat", number="1208160000000005")
    with pytest.raises(ValueError, match="no opening lot to remove"):
        remove_opening_lots(inv, folio, sec)
