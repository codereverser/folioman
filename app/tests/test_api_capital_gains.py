"""Realised capital-gains view: STCG/LTCG split, per-disposal rows, FY filter."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.services.tax_export import build_capital_gains
from folioman_app.tasks.import_csv import create_manual_transaction

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


def test_long_term_disposal_lands_in_ltcg(make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2020, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="200", on=dt.date(2024, 8, 1))

    cg = build_capital_gains(inv, "2024-25", fmv_lookup=lambda *_: None)
    assert cg["stcg_total"] == Decimal("0.00")
    assert cg["ltcg_total"] == Decimal("1000.00")
    assert len(cg["rows"]) == 1
    row = cg["rows"][0]
    assert row["term"] == "long"
    assert row["sale_value"] == Decimal("2000.00")
    assert row["gain"] == Decimal("1000.00")
    assert row["sale_value"] - row["cost"] == row["gain"]  # row ties out
    assert row["security_id"] is not None


def test_short_term_disposal_lands_in_stcg(make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2024, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="120", on=dt.date(2024, 8, 1))

    cg = build_capital_gains(inv, "2024-25", fmv_lookup=lambda *_: None)
    assert cg["ltcg_total"] == Decimal("0.00")
    assert cg["stcg_total"] == Decimal("200.00")
    assert cg["rows"][0]["term"] == "short"


def test_other_fy_disposal_excluded(make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2020, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="200", on=dt.date(2024, 8, 1))
    # Different FY → no rows.
    cg = build_capital_gains(inv, "2023-24", fmv_lookup=lambda *_: None)
    assert cg["rows"] == []
    assert cg["ltcg_total"] == Decimal("0.00")


def test_capital_gains_via_api(client, make_investor):
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2020, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="200", on=dt.date(2024, 8, 1))

    resp = client.get(f"/api/investors/{inv.id}/exports/capital-gains", {"fy": "2024-25"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["fy"] == "2024-25"
    assert Decimal(str(body["ltcg_total"])) == Decimal("1000.00")
    assert len(body["rows"]) == 1
    assert "tax advice" in body["disclaimer"].lower()


def test_capital_gains_malformed_fy_422(client, make_investor):
    inv = make_investor()
    resp = client.get(f"/api/investors/{inv.id}/exports/capital-gains", {"fy": "twenty-25"})
    assert resp.status_code == 422


def test_capital_gains_missing_investor_404(client):
    resp = client.get("/api/investors/999999/exports/capital-gains", {"fy": "2024-25"})
    assert resp.status_code == 404
