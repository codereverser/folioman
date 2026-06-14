"""CSV import + manual transaction entry."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from folioman_app.models import PartialBlock, Security, SecurityIntegrityStatus, Transaction
from folioman_app.models.jobs import ImportJob, ImportKind
from folioman_app.tasks.import_csv import process_csv

pytestmark = pytest.mark.django_db

# Equity imports require a real demat account number (BO ID). 16 digits = CDSL.
_DEMAT = "1208160000000001"
_RELIANCE = "INE002A01018"
_BASE_COLS = "security_type,name,symbol,isin,date,transaction_type,units,price"


def _eq_header(extra: str = "") -> str:
    """Canonical equity CSV header. `extra` names the one optional column
    (amount/fees/brokerage/source_ref) a test exercises, or "" for none."""
    middle = f",{extra}" if extra else ""
    return f"{_BASE_COLS}{middle},folio_number,broker\n"


def _eq_row(date, ttype, units, price, extra="", *, isin=_RELIANCE, folio=_DEMAT) -> str:
    """One equity CSV row on demat `folio`. `extra` is the optional column's value
    (matching `_eq_header(extra=...)`), or "" when the header declares none."""
    fields = ["equity", "Reliance Industries", "RELIANCE", isin, date, ttype, units, price]
    if extra != "":
        fields.append(extra)
    fields += [folio, "Zerodha"]
    return ",".join(str(f) for f in fields) + "\n"


_HEADER = _eq_header("amount")
_EQUITY = _eq_row("2024-01-15", "buy", 10, 2800, "28000")
_CRYPTO = "crypto,Bitcoin,BTC,,2024-02-01,buy,0.01,4500000,45000,,\n"


def _run_csv(investor, text: str) -> dict:
    # CSV import is disabled at the HTTP endpoint + job runner until the
    # multi-asset release; exercise the preserved parsing logic directly so it
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
    # valid folio, unparseable date -> errors after folio resolution
    bad = f"equity,Bad Row,BADSYM,,not-a-date,buy,1,1,,{_DEMAT},Zerodha\n"
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


def test_csv_equity_attaches_demat_folio_and_reconciles(make_investor):
    # An equity row carries its demat account number, so it lands in that folio and
    # reconciles (full_history here — a complete ledger with no opposing snapshot).
    inv = make_investor()
    _run_csv(inv, _HEADER + _EQUITY)
    sec = Security.objects.get(isin="INE002A01018")
    txn = Transaction.objects.get(investor=inv, security=sec)
    assert txn.folio is not None
    assert txn.folio.number == _DEMAT
    assert txn.folio.folio_type == "demat"
    assert SecurityIntegrityStatus.objects.filter(investor=inv, security=sec).exists()


def test_csv_crypto_stays_folio_less(make_investor):
    # A crypto row has no demat account — it stays folio-less and unreconciled.
    inv = make_investor()
    _run_csv(inv, _HEADER + _CRYPTO)
    sec = Security.objects.get(security_type="crypto", symbol="BTC")
    assert Transaction.objects.get(investor=inv, security=sec).folio is None
    assert not SecurityIntegrityStatus.objects.filter(investor=inv, security=sec).exists()


def test_csv_equity_requires_demat_number(make_investor):
    inv = make_investor()
    row = "equity,Reliance,RELIANCE,INE002A01018,2024-01-15,buy,10,2800,28000,,\n"
    result = _run_csv(inv, _HEADER + row)
    assert result["created"] == 0
    assert "demat account number" in result["errors"][0]["error"]
    assert Transaction.objects.filter(investor=inv).count() == 0


def test_csv_equity_rejects_malformed_demat_number(make_investor):
    inv = make_investor()
    row = "equity,Reliance,RELIANCE,INE002A01018,2024-01-15,buy,10,2800,28000,NOTAVALIDID,Zerodha\n"
    result = _run_csv(inv, _HEADER + row)
    assert result["created"] == 0
    assert "invalid demat account number" in result["errors"][0]["error"]


def test_csv_import_endpoint_creates_transactions(client, make_investor):
    inv = make_investor()
    upload = SimpleUploadedFile("txns.csv", (_HEADER + _EQUITY + _CRYPTO).encode())
    resp = client.post(f"/api/investors/{inv.id}/imports/csv", {"file": upload})
    assert resp.status_code == 201
    assert resp.json()["result"]["created"] == 2
    assert Transaction.objects.filter(investor=inv).count() == 2


def test_csv_import_endpoint_reimport_creates_no_rows(client, make_investor):
    # Done-when for E0: a canonical CSV imports, and re-importing the same file
    # adds 0 new rows (content-hash dedup is idempotent).
    inv = make_investor()
    body = (_HEADER + _EQUITY + _CRYPTO).encode()
    client.post(
        f"/api/investors/{inv.id}/imports/csv", {"file": SimpleUploadedFile("txns.csv", body)}
    )
    resp = client.post(
        f"/api/investors/{inv.id}/imports/csv", {"file": SimpleUploadedFile("txns.csv", body)}
    )
    assert resp.status_code == 201
    assert resp.json()["result"]["created"] == 0
    assert Transaction.objects.filter(investor=inv).count() == 2


def test_csv_import_endpoint_requires_owned_investor(client):
    # Investor comes from the path; an unknown/unowned investor 404s.
    upload = SimpleUploadedFile("txns.csv", (_HEADER + _EQUITY).encode())
    resp = client.post("/api/investors/999999/imports/csv", {"file": upload})
    assert resp.status_code == 404


# --- manual transaction entry ----------------------------------------------
# Authoring is gated off by default in the first release (CAS/eCAS only); these
# exercise the endpoint with the flag flipped on, plus the disabled-by-default
# guard. create_manual_transaction() itself stays covered directly elsewhere.


def _post(client, url, payload):
    return client.post(url, data=payload, content_type="application/json")


def test_manual_transaction_disabled_by_default(client, make_investor):
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
        },
    )
    assert resp.status_code == 503
    assert Transaction.objects.filter(investor=inv).count() == 0


@override_settings(MANUAL_TRANSACTIONS_ENABLED=True)
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


@override_settings(MANUAL_TRANSACTIONS_ENABLED=True)
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


@override_settings(MANUAL_TRANSACTIONS_ENABLED=True)
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


@override_settings(MANUAL_TRANSACTIONS_ENABLED=True)
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
    header = _eq_header("brokerage")
    row = _eq_row("2024-01-15", "buy", 10, 2800, "200")
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
    header = _eq_header("fees")
    row_a = _eq_row("2024-01-15", "sell", 10, 2800, "12")
    row_b = _eq_row("2024-01-15", "sell", 10, 2800, "15")
    result = _run_csv(inv, header + row_a + row_b)
    assert result["created"] == 2
    assert Transaction.objects.filter(investor=inv).count() == 2


def test_csv_identical_fills_with_distinct_trade_ids_not_deduped(make_investor):
    """Two genuine fills with identical (symbol,date,type,qty,price) but distinct
    broker trade_ids must both persist — without source_ref in the dedup key the
    second would collapse into the first."""
    inv = make_investor()
    header = _eq_header("source_ref")
    row_a = _eq_row("2024-01-15", "buy", 10, 2800, "TRADE001")
    row_b = _eq_row("2024-01-15", "buy", 10, 2800, "TRADE002")
    result = _run_csv(inv, header + row_a + row_b)
    assert result["created"] == 2
    assert Transaction.objects.filter(investor=inv).count() == 2
    assert set(Transaction.objects.filter(investor=inv).values_list("source_ref", flat=True)) == {
        "TRADE001",
        "TRADE002",
    }


def test_csv_same_trade_id_reimport_is_idempotent(make_investor):
    """Re-importing the same fill (same trade_id) hashes the same → 0 new rows."""
    inv = make_investor()
    header = _eq_header("source_ref")
    row = _eq_row("2024-01-15", "buy", 10, 2800, "TRADE001")
    _run_csv(inv, header + row)
    result = _run_csv(inv, header + row)
    assert result["created"] == 0
    assert Transaction.objects.filter(investor=inv).count() == 1


# --- incomplete history: orphan sells & unknown openings -------------------


def test_csv_orphan_sell_flags_incomplete_and_records_partial_block(make_investor):
    """A tradebook that starts mid-history has a sell with no prior buy. The import
    must complete (not raise), flag the security's ledger incomplete, and record a
    partial block — never fabricate cost basis for the orphaned lots."""
    inv = make_investor()
    # sell 10 then buy 5: the sell underflows (no prior buy); net is -5.
    rows = _eq_row("2021-06-01", "sell", 10, 2400) + _eq_row("2022-06-01", "buy", 5, 2600)
    result = _run_csv(inv, _eq_header() + rows)
    assert result["created"] == 2
    assert result["incomplete_history"][0]["reason"] == "orphan_sell"
    # Selling 10 needs 10 units present at that moment -> 10 must predate the window
    # (the later buy of 5 doesn't reduce what was required at the sell). Net is -5.
    assert Decimal(result["incomplete_history"][0]["missing_prior_units"]) == Decimal("10")
    assert Decimal(result["incomplete_history"][0]["net_units"]) == Decimal("-5")

    sec = Security.objects.get(isin=_RELIANCE)
    assert Transaction.objects.filter(investor=inv, security=sec).count() == 2
    assert not Transaction.objects.filter(
        investor=inv, security=sec, cost_basis_complete=True
    ).exists()
    pb = PartialBlock.objects.get(investor=inv, security=sec)
    assert pb.opening_units == Decimal("10")
    # The cost_basis() filter (the single gate feeding FIFO / realized P&L / 112A /
    # valuation) excludes the whole bucket, so no bogus gains are computed.
    assert not Transaction.objects.cost_basis().filter(investor=inv, security=sec).exists()


def test_csv_full_history_bucket_stays_complete(make_investor):
    """Buy-before-sell within the window is solvent: no partial block, rows stay
    cost_basis_complete."""
    inv = make_investor()
    rows = _eq_row("2024-01-15", "buy", 10, 2800) + _eq_row("2024-06-15", "sell", 4, 3100)
    result = _run_csv(inv, _eq_header() + rows)
    assert "incomplete_history" not in result
    sec = Security.objects.get(isin=_RELIANCE)
    assert (
        Transaction.objects.filter(investor=inv, security=sec, cost_basis_complete=False).count()
        == 0
    )
    assert not PartialBlock.objects.filter(investor=inv, security=sec).exists()


def test_csv_earlier_import_upgrades_partial_to_complete(make_investor):
    """Importing an earlier-period tradebook that supplies the missing buys flips a
    previously-orphaned equity ledger back to complete and drops the partial block —
    order-independent convergence, like the MF chaining path."""
    inv = make_investor()
    # First: a mid-history file -> orphan sell of 10 (only 5 bought here).
    first = _eq_row("2021-06-01", "sell", 10, 2400) + _eq_row("2022-06-01", "buy", 5, 2600)
    _run_csv(inv, _eq_header() + first)
    sec = Security.objects.get(isin=_RELIANCE)
    assert PartialBlock.objects.filter(investor=inv, security=sec).exists()

    # Then: an earlier-period file supplying the 10 units the orphan sell consumed.
    earlier = _eq_row("2019-01-10", "buy", 10, 1900)
    result = _run_csv(inv, _eq_header() + earlier)
    assert "incomplete_history" not in result
    assert not PartialBlock.objects.filter(investor=inv, security=sec).exists()
    # Every row (across both imports) is now complete.
    assert not Transaction.objects.filter(
        investor=inv, security=sec, cost_basis_complete=False
    ).exists()


@override_settings(MANUAL_TRANSACTIONS_ENABLED=True)
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
