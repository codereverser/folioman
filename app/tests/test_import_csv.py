"""CSV import + manual transaction entry."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from folioman_app.models import Security, SecurityIntegrityStatus, Transaction
from folioman_app.models.jobs import ImportJob, ImportKind
from folioman_app.tasks.import_csv import process_csv

pytestmark = pytest.mark.django_db

_HEADER = "security_type,name,symbol,isin,date,transaction_type,units,price,amount\n"
_EQUITY = "equity,Reliance Industries,RELIANCE,INE002A01018,2024-01-15,buy,10,2800,28000\n"
_CRYPTO = "crypto,Bitcoin,BTC,,2024-02-01,buy,0.01,4500000,45000\n"


def _run_csv(investor, text: str) -> dict:
    # CSV import is disabled at the HTTP endpoint + job runner until the
    # multi-asset phase; exercise the preserved parsing logic directly so it
    # stays covered for re-enable.
    job = ImportJob.objects.create(investor=investor, kind=ImportKind.CSV)
    return process_csv(job, text.encode(), "")


def test_csv_creates_transactions_and_securities(make_investor):
    inv = make_investor()
    result = _run_csv(inv, _HEADER + _EQUITY + _CRYPTO)
    assert result["rows"] == 2
    assert result["created"] == 2
    assert result["errors"] == []
    assert Transaction.objects.filter(investor=inv).count() == 2
    assert Security.objects.filter(isin="INE002A01018").exists()
    assert Security.objects.filter(security_type="crypto", symbol="BTC").exists()


def test_csv_partial_success_collects_row_errors(make_investor):
    inv = make_investor()
    bad = "equity,Bad Row,BADSYM,,not-a-date,buy,1,1,\n"  # valid security, unparseable date
    result = _run_csv(inv, _HEADER + _EQUITY + bad)
    assert result["created"] == 1  # the good row still persists
    assert result["skipped"] == 1
    assert result["errors"][0]["row"] == 3
    assert "date" in result["errors"][0]["error"].lower()
    assert Transaction.objects.filter(investor=inv).count() == 1
    # the bad row's atomic savepoint rolled back its security upsert too
    assert not Security.objects.filter(symbol="BADSYM").exists()


def test_csv_reimport_is_idempotent(make_investor):
    inv = make_investor()
    _run_csv(inv, _HEADER + _EQUITY)
    result = _run_csv(inv, _HEADER + _EQUITY)
    assert result["created"] == 0
    assert Transaction.objects.filter(investor=inv).count() == 1


def test_reconcile_failure_keeps_data_and_records_error(make_investor, monkeypatch):
    """A post-commit reconcile failure must not discard the imported rows: the
    error is collected in the result and the data stays committed. (The runner
    then maps a non-empty reconcile_errors to COMPLETED_WITH_WARNINGS — covered
    via the CAS path in test_import_cas.)"""
    from folioman_app.tasks import reconcile as reconcile_mod

    def boom(investor, security, **kwargs):
        raise RuntimeError("reconcile blew up")

    monkeypatch.setattr(reconcile_mod, "reconcile_security", boom)

    inv = make_investor()
    result = _run_csv(inv, _HEADER + _EQUITY)

    assert result["reconcile_errors"][0]["error"] == "reconcile blew up"
    assert Transaction.objects.filter(investor=inv).count() == 1  # data committed


def test_csv_rows_are_folio_less_so_not_reconciled(make_investor):
    # The parked generic CSV importer writes folio-less rows; integrity is
    # per-(security, folio), so these aren't reconciled until the templated
    # per-broker importer (multi-asset phase) attaches a folio. The rows persist.
    inv = make_investor()
    _run_csv(inv, _HEADER + _EQUITY)
    sec = Security.objects.get(isin="INE002A01018")
    assert Transaction.objects.filter(investor=inv, security=sec).count() == 1
    assert not SecurityIntegrityStatus.objects.filter(investor=inv, security=sec).exists()


def test_csv_import_endpoint_is_disabled(client, make_investor):
    # Disabled until the multi-asset phase — the endpoint rejects the upload and
    # persists nothing. (process_csv logic is still covered via _run_csv above.)
    inv = make_investor()
    upload = SimpleUploadedFile("txns.csv", (_HEADER + _EQUITY + _CRYPTO).encode())
    resp = client.post(f"/api/investors/{inv.id}/imports/csv", {"file": upload})
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"].lower()
    assert Transaction.objects.filter(investor=inv).count() == 0


# --- manual transaction entry ----------------------------------------------


def _post(client, url, payload):
    return client.post(url, data=payload, content_type="application/json")


def test_manual_transaction_creates_and_reconciles(client, make_investor):
    inv = make_investor()
    resp = _post(
        client,
        f"/api/investors/{inv.id}/transactions",
        {
            "security_type": "equity",
            "folio_number": "1208160000000001",
            "broker": "ZERODHA",
            "name": "TCS",
            "symbol": "TCS",
            "isin": "INE467B01029",
            "date": "2024-03-01",
            "transaction_type": "buy",
            "units": "5",
            "price": "3800",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "manual"
    assert Decimal(str(body["units"])) == Decimal("5")
    sec = Security.objects.get(isin="INE467B01029")
    assert SecurityIntegrityStatus.objects.filter(investor=inv, security=sec).exists()


def test_manual_transaction_allows_intentional_duplicate(client, make_investor):
    inv = make_investor()
    payload = {
        "security_type": "equity",
        "name": "TCS",
        "isin": "INE467B01029",
        "folio_number": "1208160000000001",
        "broker": "ZERODHA",
        "date": "2024-03-01",
        "transaction_type": "buy",
        "units": "5",
        "price": "3800",
    }
    _post(client, f"/api/investors/{inv.id}/transactions", payload)
    _post(client, f"/api/investors/{inv.id}/transactions", payload)
    assert Transaction.objects.filter(investor=inv).count() == 2  # no dedup on manual


def test_manual_transaction_invalid_security_422(client, make_investor):
    inv = make_investor()
    # equity needs symbol or isin — neither given.
    resp = _post(
        client,
        f"/api/investors/{inv.id}/transactions",
        {
            "security_type": "equity",
            "folio_number": "1208160000000001",
            "broker": "ZERODHA",
            "name": "No identifiers",
            "date": "2024-03-01",
            "transaction_type": "buy",
            "units": "5",
            "price": "3800",
        },
    )
    assert resp.status_code == 422


def test_manual_to_missing_investor_404(client):
    resp = _post(
        client,
        "/api/investors/999999/transactions",
        {
            "security_type": "equity",
            "folio_number": "1208160000000001",
            "broker": "ZERODHA",
            "name": "X",
            "isin": "INE002A01018",
            "date": "2024-03-01",
            "transaction_type": "buy",
            "units": "1",
            "price": "1",
        },
    )
    assert resp.status_code == 404


def test_list_transactions(client, make_investor):
    inv = make_investor()
    _run_csv(inv, _HEADER + _EQUITY + _CRYPTO)
    rows = client.get(f"/api/investors/{inv.id}/transactions").json()
    assert len(rows) == 2


# --- buy-side brokerage flows into cost basis ------------------------------


def test_csv_brokerage_column_enters_cost_basis(make_investor):
    from folioman_app.mappers import to_core_transaction
    from folioman_core.fifo import apply_fifo

    inv = make_investor()
    header = "security_type,name,symbol,isin,date,transaction_type,units,price,brokerage\n"
    row = "equity,Reliance Industries,RELIANCE,INE002A01018,2024-01-15,buy,10,2800,200\n"
    result = _run_csv(inv, header + row)
    assert result["created"] == 1

    txn = Transaction.objects.get(investor=inv)
    assert txn.brokerage == Decimal("200")

    fifo = apply_fifo([to_core_transaction(txn)])
    # Cost = 10 * 2800 + 200 brokerage = 28200 (not 28000).
    assert fifo.invested == Decimal("28200")


def test_csv_sells_differing_only_in_fees_are_not_deduped(make_investor):
    """Two otherwise-identical sells with different STT are distinct rows — the
    dedup key must not collapse them (would under-report disposals)."""
    inv = make_investor()
    header = "security_type,name,symbol,isin,date,transaction_type,units,price,fees\n"
    row_a = "equity,Reliance Industries,RELIANCE,INE002A01018,2024-01-15,sell,10,2800,12\n"
    row_b = "equity,Reliance Industries,RELIANCE,INE002A01018,2024-01-15,sell,10,2800,15\n"
    result = _run_csv(inv, header + row_a + row_b)
    assert result["created"] == 2
    assert Transaction.objects.filter(investor=inv).count() == 2


def test_manual_transaction_accepts_brokerage(client, make_investor):
    inv = make_investor()
    resp = _post(
        client,
        f"/api/investors/{inv.id}/transactions",
        {
            "security_type": "equity",
            "folio_number": "1208160000000001",
            "broker": "ZERODHA",
            "name": "TCS",
            "isin": "INE467B01029",
            "date": "2024-03-01",
            "transaction_type": "buy",
            "units": "5",
            "price": "3800",
            "brokerage": "95",
        },
    )
    assert resp.status_code == 201
    assert Decimal(str(resp.json()["brokerage"])) == Decimal("95")
    assert Transaction.objects.get(investor=inv).brokerage == Decimal("95")
