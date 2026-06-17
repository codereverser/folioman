"""Opening lots and identity remap for eCAS-only equities."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import Folio, Security, SecurityIntegrityStatus, Transaction
from folioman_app.services.identity_remap import apply_identity_remap
from folioman_app.services.opening_lots import record_opening_lot
from folioman_app.tasks.import_csv import create_manual_transaction
from folioman_app.tasks.import_ecas import persist_ecas_statement
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
