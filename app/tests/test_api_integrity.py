"""Integrity API: list statuses, acknowledge a mismatch, recompute."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import Folio, Security, Transaction
from folioman_app.tasks.import_csv import create_manual_transaction
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
# The manual ledger and the eCAS holding must share the demat account number so
# they reconcile in the same (security, folio) bucket.
_DEMAT = "1208160000000001"


def _equity_txn(inv, *, txn_type, units, price, on=dt.date(2020, 1, 1)):
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


def _ecas_holding(inv, *, isin, units):
    statement = EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number=_DEMAT, broker="ZERODHA"),
                holdings=[
                    EcasHoldingLine(
                        security=CoreSecurity(
                            type=SecurityType.EQUITY, name="Reliance Industries", isin=isin
                        ),
                        units=Decimal(units),
                    )
                ],
            )
        ],
    )
    persist_ecas_statement(inv, statement, source_ref="e")


def test_list_integrity_reflects_import(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="100", price="2000")
    rows = client.get(f"/api/investors/{inv.id}/integrity").json()
    assert len(rows) == 1
    assert rows[0]["security"]["isin"] == _ISIN
    assert rows[0]["status"] == "full_history"
    assert rows[0]["tax_safe"] is True


def test_list_is_investor_scoped(client, make_investor):
    a = make_investor()
    b = make_investor()
    _equity_txn(a, txn_type="buy", units="10", price="100")
    assert len(client.get(f"/api/investors/{a.id}/integrity").json()) == 1
    assert client.get(f"/api/investors/{b.id}/integrity").json() == []


def test_acknowledge_mismatch(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="100", price="100")  # ledger = 100
    _ecas_holding(inv, isin=_ISIN, units="90")  # demat = 90 -> mismatch
    sec = Security.objects.get(isin=_ISIN)

    rows = client.get(f"/api/investors/{inv.id}/integrity").json()
    assert rows[0]["status"] == "mismatch"

    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    resp = client.post(f"/api/investors/{inv.id}/integrity/{sec.id}/{folio.id}/acknowledge")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "user_acknowledged"
    assert body["tax_safe"] is False


def test_acknowledge_survives_recompute(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="100", price="100")
    _ecas_holding(inv, isin=_ISIN, units="90")
    sec = Security.objects.get(isin=_ISIN)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    client.post(f"/api/investors/{inv.id}/integrity/{sec.id}/{folio.id}/acknowledge")

    # A full recompute must not silently revert the acknowledgement to mismatch.
    rows = client.post(f"/api/investors/{inv.id}/integrity/recompute").json()
    assert rows[0]["status"] == "user_acknowledged"


def test_acknowledge_unknown_security_404(client, make_investor):
    inv = make_investor()
    sec = Security.objects.create(
        security_type=SecurityType.EQUITY.value, name="Untracked", isin="INE111111111"
    )
    resp = client.post(f"/api/investors/{inv.id}/integrity/{sec.id}/acknowledge")
    assert resp.status_code == 404


def test_recompute_returns_statuses(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100")
    rows = client.post(f"/api/investors/{inv.id}/integrity/recompute").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "full_history"


def test_status_reports_ledger_and_snapshot_dates(client, make_investor):
    # The ledger reaches the latest transaction date; the snapshot reaches the
    # eCAS observation date. Both surface so the UI can date each side.
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="100", price="100", on=dt.date(2020, 1, 1))
    _ecas_holding(inv, isin=_ISIN, units="90")  # statement_date 2025-06-01
    row = client.get(f"/api/investors/{inv.id}/integrity").json()[0]
    assert row["ledger_through"] == "2020-01-01"
    assert row["snapshot_as_of"] == "2025-06-01"


def test_full_history_has_no_snapshot_date(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2021, 7, 2))
    row = client.get(f"/api/investors/{inv.id}/integrity").json()[0]
    assert row["ledger_through"] == "2021-07-02"
    assert row["snapshot_as_of"] is None


def test_unacknowledge_reverts_to_mismatch(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="100", price="100")
    _ecas_holding(inv, isin=_ISIN, units="90")  # mismatch
    sec = Security.objects.get(isin=_ISIN)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)

    client.post(f"/api/investors/{inv.id}/integrity/{sec.id}/{folio.id}/acknowledge")
    # Taking the acknowledgement back exposes the unresolved gap again, and a
    # later recompute must NOT silently re-suppress it.
    body = client.post(
        f"/api/investors/{inv.id}/integrity/{sec.id}/{folio.id}/unacknowledge"
    ).json()
    assert body["status"] == "mismatch"
    assert body["tax_safe"] is False
    rows = client.post(f"/api/investors/{inv.id}/integrity/recompute").json()
    assert rows[0]["status"] == "mismatch"


def test_unacknowledge_unknown_status_404(client, make_investor):
    inv = make_investor()
    sec = Security.objects.create(
        security_type=SecurityType.EQUITY.value, name="Untracked", isin="INE222222222"
    )
    folio = Folio.objects.create(investor=inv, number="X", folio_type="demat")
    resp = client.post(f"/api/investors/{inv.id}/integrity/{sec.id}/{folio.id}/unacknowledge")
    assert resp.status_code == 404


def test_record_opening_lot_api(client, make_investor):
    inv = make_investor()
    isin = "INE418H01026"
    statement = EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number=_DEMAT, broker="ZERODHA"),
                holdings=[
                    EcasHoldingLine(
                        security=CoreSecurity(
                            type=SecurityType.EQUITY, name="HDB Financial Services", isin=isin
                        ),
                        units="50",
                    )
                ],
            )
        ],
    )
    persist_ecas_statement(inv, statement, source_ref="ecas")
    sec = Security.objects.get(isin=isin)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    resp = client.post(
        f"/api/investors/{inv.id}/integrity/{sec.id}/{folio.id}/record-opening-lot",
        {
            "classification": "ipo_allotment",
            "date": "2024-05-01",
            "price": "700",
        },
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 1
    assert body["integrity"]["status"] == "reconciled"


def test_apply_identity_remap_api(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100")
    sec = Security.objects.get(isin=_ISIN)
    folio = Folio.objects.get(investor=inv, number=_DEMAT)
    resp = client.post(
        f"/api/investors/{inv.id}/integrity/{sec.id}/{folio.id}/apply-identity-remap",
        {"to_isin": "INE002A01099", "to_name": "Reliance Industries Ltd", "to_symbol": "RELIANCE9"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["transactions_updated"] == 1
    assert Transaction.objects.filter(investor=inv, security__isin="INE002A01099").count() == 1
