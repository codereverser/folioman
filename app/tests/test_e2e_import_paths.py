"""End-to-end import paths over the HTTP API: eCAS, CSV, manual — plus the
cross-source mismatch the integrity layer surfaces and the 112A export withholds.
Completes the four-path import matrix (CAS PDF is in test_e2e_cas_path).

eCAS reads are mocked with synthetic statements (real ISINs, fake identity and
units, no PII); manual entry uses real client payloads. Generic CSV import is
disabled until the multi-asset release. The mismatch case uses an equity (cost
history from manual entry, holding from eCAS) so it's 112A-eligible by type —
independent of fund metadata.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from folioman_app.models import Holding
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

_RELIANCE = "INE002A01018"
_BOND = "INE020B08DG9"


def _post(client, url, payload=None):
    return client.post(url, data=payload or {}, content_type="application/json")


def _upload(client, investor_id, kind, name, content, password=None):
    data = {"file": SimpleUploadedFile(name, content)}
    if password is not None:
        data["password"] = password
    return client.post(f"/api/investors/{investor_id}/imports/{kind}", data)


# --- eCAS path: holdings -> snapshot-only integrity -------------------------


def _ecas_two_holdings() -> EcasStatement:
    reliance = CoreSecurity(type=SecurityType.EQUITY, name="Reliance Industries", isin=_RELIANCE)
    bond = CoreSecurity(type=SecurityType.BOND, name="REC Ltd Bond", isin=_BOND)
    return EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number="1208160001234567", broker="ZERODHA"),
                holdings=[EcasHoldingLine(security=reliance, units="10", value_observed="28500")],
            ),
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number="IN30021412345678", broker="HDFC SEC"),
                holdings=[EcasHoldingLine(security=bond, units="5", value_observed="50000")],
            ),
        ],
    )


def test_ecas_path_holdings_then_snapshot_only_integrity(client, patch_cas, make_parsed_cas):
    # The eCAS identifies its own investor by (primary owner) PAN.
    patch_cas(make_parsed_cas(ecas=_ecas_two_holdings()))

    upload = SimpleUploadedFile("ecas.pdf", b"%PDF synthetic")
    resp = client.post("/api/imports/cas", {"file": upload, "password": "x"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "success"
    assert body["result"]["detected"] == "ecas"
    assert body["result"]["holdings_created"] == 2
    inv_id = body["investor_id"]
    assert Holding.objects.filter(investor_id=inv_id).count() == 2

    # Holdings but no transactions -> snapshot only, not tax-safe (no cost basis).
    statuses = client.get(f"/api/investors/{inv_id}/integrity").json()
    assert len(statuses) == 2
    assert all(s["status"] == "snapshot_only" for s in statuses)
    assert all(s["tax_safe"] is False for s in statuses)


# --- cross-source mismatch: surfaced by integrity, withheld from 112A -------


def _ecas_reliance(units: str) -> EcasStatement:
    sec = CoreSecurity(type=SecurityType.EQUITY, name="Reliance Industries", isin=_RELIANCE)
    return EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number="1208160009999999", broker="ZERODHA"),
                holdings=[EcasHoldingLine(security=sec, units=units, value_observed="9000")],
            )
        ],
    )


_CSV_HEADER = (
    "security_type,name,symbol,isin,date,transaction_type,units,price,amount,folio_number,broker\n"
)


@override_settings(MANUAL_TRANSACTIONS_ENABLED=True)
def test_cross_source_mismatch_withheld_from_112a(
    client, make_investor, patch_cas, make_parsed_cas
):
    inv = make_investor()
    # The eCAS upload below resolves to this investor by PAN — set it to match the
    # statement identity so the cross-source check compares the same investor.
    inv.set_pan("ABCDE1234F")
    inv.save()
    # Cost history via manual entry: net 60 units, incl. a long-term sale (a real
    # LTCG). (CSV import is disabled until the multi-asset release; manual entry is
    # the live equity-transaction path.)
    txns_url = f"/api/investors/{inv.id}/transactions"
    base = {
        "security_type": "equity",
        "name": "Reliance Industries",
        "symbol": "RELIANCE",
        "isin": _RELIANCE,
        # Same demat account number as the eCAS holding below -> same folio ->
        # the cross-source check compares them.
        "folio_number": "1208160009999999",
        "broker": "ZERODHA",
    }
    assert (
        _post(
            client,
            txns_url,
            {
                **base,
                "date": "2020-01-01",
                "transaction_type": "buy",
                "units": "100",
                "price": "50",
            },
        ).status_code
        == 201
    )
    assert (
        _post(
            client,
            txns_url,
            {
                **base,
                "date": "2024-08-01",
                "transaction_type": "sell",
                "units": "40",
                "price": "200",
            },
        ).status_code
        == 201
    )

    # Demat eCAS says 50 for the same ISIN -> the sources disagree.
    patch_cas(make_parsed_cas(ecas=_ecas_reliance("50")))
    upload = SimpleUploadedFile("ecas.pdf", b"%PDF")
    assert client.post("/api/imports/cas", {"file": upload, "password": "x"}).status_code == 201

    statuses = client.get(f"/api/investors/{inv.id}/integrity").json()
    assert len(statuses) == 1
    assert statuses[0]["status"] == "mismatch"
    assert statuses[0]["tax_safe"] is False

    # The real LTCG is withheld from the tax export until the gap is acknowledged.
    url = f"/api/investors/{inv.id}/exports/schedule-112a"
    assert _post(client, url, {"fy": "2024-25"}).json()["row_count"] == 0
    forced = _post(client, url, {"fy": "2024-25", "include_unreconciled": True}).json()
    assert forced["row_count"] == 1


# --- CSV path: canonical transaction upload -----------------------------------


def test_csv_import_path_creates_transactions(client, make_investor):
    # A canonical-CSV upload runs end to end and lands ledger rows for the investor.
    inv = make_investor()
    row = (
        f"equity,Reliance,RELIANCE,{_RELIANCE},2024-01-15,buy,10,2800,28000,"
        "1208160000000001,ZERODHA\n"
    )
    csv = _CSV_HEADER + row
    resp = _upload(client, inv.id, "csv", "txns.csv", csv.encode())
    assert resp.status_code == 201
    assert len(client.get(f"/api/investors/{inv.id}/transactions").json()) == 1


# --- manual entry: one transaction --------------------------------------------


@override_settings(MANUAL_TRANSACTIONS_ENABLED=True)
def test_manual_entry_single_transaction_and_integrity(client, make_investor):
    inv = make_investor()
    resp = _post(
        client,
        f"/api/investors/{inv.id}/transactions",
        {
            "security_type": "equity",
            "name": "TCS",
            "symbol": "TCS",
            "isin": "INE467B01029",
            "folio_number": "1208160000000002",
            "broker": "ZERODHA",
            "date": "2024-03-01",
            "transaction_type": "buy",
            "units": "5",
            "price": "3800",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["source"] == "manual"

    assert len(client.get(f"/api/investors/{inv.id}/transactions").json()) == 1
    statuses = client.get(f"/api/investors/{inv.id}/integrity").json()
    assert statuses[0]["status"] == "full_history"
