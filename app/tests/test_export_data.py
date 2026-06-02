"""Free-tier data exports: holdings CSV + round-trippable transactions CSV."""

from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal

import pytest
from folioman_app.models import NAVHistory, Security, Transaction
from folioman_app.models.jobs import ImportJob, ImportKind
from folioman_app.tasks.import_csv import create_manual_transaction, process_csv
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_core.models import SecurityType
from folioman_core.models.cas import (
    Depository,
    EcasAccountBlock,
    EcasHoldingLine,
    EcasStatement,
)
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.security import Security as CoreSecurity

pytestmark = pytest.mark.django_db

_ISIN = "INE002A01018"


def _parse_csv(response):
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]
    return list(csv.DictReader(io.StringIO(response.content.decode())))


def _equity_txn(inv, *, txn_type, units, price, on):
    return create_manual_transaction(
        inv,
        {
            "security_type": "equity",
            "name": "Reliance Industries",
            "symbol": "RELIANCE",
            "isin": _ISIN,
            "folio_number": "1208160000000001",
            "broker": "ZERODHA",
            "date": on,
            "transaction_type": txn_type,
            "units": Decimal(units),
            "price": Decimal(price),
        },
    )


def test_holdings_csv_values_transaction_position(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="100", price="2000", on=dt.date(2024, 1, 1))
    _equity_txn(inv, txn_type="sell", units="40", price="2500", on=dt.date(2024, 6, 1))
    sec = Security.objects.get(isin=_ISIN)
    NAVHistory.objects.create(security=sec, date=dt.date(2025, 6, 1), nav=Decimal("2850"))

    rows = _parse_csv(client.get(f"/api/investors/{inv.id}/exports/holdings"))
    assert len(rows) == 1
    row = rows[0]
    assert row["isin"] == _ISIN
    assert row["basis"] == "transactions"
    assert Decimal(row["units"]) == Decimal("60")  # 100 - 40
    assert Decimal(row["value_inr"]) == Decimal("171000.000000")  # 60 * 2850
    assert row["integrity_status"] == "full_history"


def test_holdings_csv_includes_ecas_snapshot_without_price(client, make_investor):
    inv = make_investor()
    ecas = EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number="120816000000", broker="Z"),
                holdings=[
                    EcasHoldingLine(
                        security=CoreSecurity(
                            type=SecurityType.BOND, name="REC Bond", isin="INE020B08DG9"
                        ),
                        units="5",
                    )
                ],
            )
        ],
    )
    persist_ecas_statement(inv, ecas, source_ref="e")
    rows = _parse_csv(client.get(f"/api/investors/{inv.id}/exports/holdings"))
    assert len(rows) == 1
    assert rows[0]["basis"] == "holdings"
    assert rows[0]["value_inr"] == ""  # no NAVHistory price


def test_fully_sold_security_omitted(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2024, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="120", on=dt.date(2024, 6, 1))
    rows = _parse_csv(client.get(f"/api/investors/{inv.id}/exports/holdings"))
    assert rows == []  # net zero — not currently held


def test_transactions_csv_round_trips_into_another_investor(client, make_investor):
    src = make_investor()
    _equity_txn(src, txn_type="buy", units="10", price="100", on=dt.date(2024, 1, 1))
    _equity_txn(src, txn_type="sell", units="4", price="150", on=dt.date(2024, 6, 1))

    csv_text = client.get(f"/api/investors/{src.id}/exports/transactions").content.decode()

    # Re-import the exported CSV into a fresh investor. CSV import is disabled at
    # the runner/endpoint (multi-asset release), so round-trip via the preserved
    # parser directly to verify the exported CSV re-imports faithfully.
    dest = make_investor()
    job = ImportJob.objects.create(investor=dest, kind=ImportKind.CSV)
    result = process_csv(job, csv_text.encode(), "")
    assert result["created"] == 2
    assert Transaction.objects.filter(investor=dest).count() == 2
    assert Security.objects.filter(isin=_ISIN).count() == 1  # same security, not duplicated


def test_export_missing_investor_404(client):
    assert client.get("/api/investors/999999/exports/holdings").status_code == 404
    assert client.get("/api/investors/999999/exports/transactions").status_code == 404
