"""Schedule 112A export API: LTCG rows, grandfathering, integrity filter."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.services.tax_export import build_schedule_112a
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

_ISIN = "INE002A01018"  # Reliance — a real, 112A-eligible equity ISIN


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


# --- service-level (deterministic via injected FMV) -------------------------


def test_grandfathered_ltcg_row(make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="50", on=dt.date(2017, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="150", on=dt.date(2024, 8, 1))

    result = build_schedule_112a(inv, "2024-25", fmv_lookup=lambda _isin, _on: Decimal("90"))
    assert result["row_count"] == 1
    row = result["rows"][0]
    assert row["Share/Unit acquired(1a)"] == "BE"  # before 01-Feb-2018
    assert row["ISIN Code(2)"] == _ISIN
    # Grandfathered cost basis = max(actual 50, min(FMV 90, sale 150)) = 90/unit.
    assert row["Cost of acquisition without indexation(7)"] == "900.00"


def test_short_term_disposal_excluded(make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2024, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="120", on=dt.date(2024, 8, 1))
    # Held < 12 months -> STCG, not on Schedule 112A.
    result = build_schedule_112a(inv, "2024-25", fmv_lookup=lambda *_: None)
    assert result["row_count"] == 0


def test_mismatch_excluded_unless_forced(make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="100", price="100", on=dt.date(2020, 1, 1))
    _equity_txn(inv, txn_type="sell", units="40", price="200", on=dt.date(2024, 8, 1))
    # Conflicting demat holding (50 vs ledger net 60) -> MISMATCH.
    ecas = EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 3, 31),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number="1208160000000001", broker="ZERODHA"),
                holdings=[
                    EcasHoldingLine(
                        security=CoreSecurity(
                            type=SecurityType.EQUITY, name="Reliance Industries", isin=_ISIN
                        ),
                        units="50",
                    )
                ],
            )
        ],
    )
    persist_ecas_statement(inv, ecas, source_ref="ecas")

    default = build_schedule_112a(inv, "2024-25", fmv_lookup=lambda *_: None)
    assert default["row_count"] == 0  # mismatch excluded by default

    forced = build_schedule_112a(
        inv, "2024-25", include_unreconciled=True, fmv_lookup=lambda *_: None
    )
    assert forced["row_count"] == 1


# --- API end-to-end (post-2018 acquisition -> no FMV needed) ----------------


def _post(client, url, payload):
    return client.post(url, data=payload, content_type="application/json")


def test_export_via_api(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2020, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="200", on=dt.date(2024, 8, 1))

    resp = _post(client, f"/api/investors/{inv.id}/exports/schedule-112a", {"fy": "2024-25"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 1
    assert len(body["columns"]) == 15
    assert body["rows"][0]["Share/Unit acquired(1a)"] == "AE"  # after 31-Jan-2018
    # Every worksheet response frames itself as a reviewable draft, not a filing.
    assert body["is_draft"] is True
    assert body["title"] == "Capital-gains worksheet (for review)"
    disclaimer = body["disclaimer"].lower()
    assert "tax advice" in disclaimer
    assert "qualified tax professional" in disclaimer


def test_export_empty_when_no_disposals(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2020, 1, 1))
    resp = _post(client, f"/api/investors/{inv.id}/exports/schedule-112a", {"fy": "2024-25"})
    assert resp.json()["row_count"] == 0


def test_export_missing_investor_404(client):
    resp = _post(client, "/api/investors/999999/exports/schedule-112a", {"fy": "2024-25"})
    assert resp.status_code == 404


def test_export_malformed_fy_422(client, make_investor):
    inv = make_investor()
    resp = _post(client, f"/api/investors/{inv.id}/exports/schedule-112a", {"fy": "twenty-25"})
    assert resp.status_code == 422
