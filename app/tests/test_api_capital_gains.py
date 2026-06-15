"""Realised capital-gains view: STCG/LTCG split, per-disposal rows, FY filter."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models.jobs import ImportJob, ImportKind
from folioman_app.services.tax_export import build_capital_gains, build_schedule_112a
from folioman_app.tasks.import_csv import create_manual_transaction, process_csv

pytestmark = pytest.mark.django_db

_ISIN = "INE002A01018"  # Reliance — a real, 112A-eligible equity ISIN
_DEMAT = "1208160000000001"
_EQ_HEADER = (
    "security_type,name,symbol,isin,date,transaction_type,units,price,folio_number,broker\n"
)


def _eq_row(date, ttype, units, price) -> str:
    fields = [
        "equity",
        "Reliance Industries",
        "RELIANCE",
        _ISIN,
        date,
        ttype,
        units,
        price,
        _DEMAT,
        "Zerodha",
    ]
    return ",".join(str(f) for f in fields) + "\n"


def _run_csv(investor, text: str) -> dict:
    job = ImportJob.objects.create(investor=investor, kind=ImportKind.CSV)
    return process_csv(job, text.encode(), "")


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


def test_grandfathering_unavailable_flagged_when_fmv_missing(make_investor):
    # A pre-2018 lot sold long-term: without a 31-Jan-2018 FMV, the grandfathering
    # benefit can't be applied, so the gain is overstated — the row must say so.
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2017, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="300", on=dt.date(2024, 8, 1))

    cg = build_capital_gains(inv, "2024-25", fmv_lookup=lambda *_: None)
    assert cg["rows"][0]["grandfathering_unavailable"] is True


def test_grandfathering_available_not_flagged(make_investor):
    # Same lot, but a FMV is available → benefit applies, no warning.
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2017, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="300", on=dt.date(2024, 8, 1))

    cg = build_capital_gains(inv, "2024-25", fmv_lookup=lambda *_: Decimal("150"))
    assert cg["rows"][0]["grandfathering_unavailable"] is False


def test_post_2018_lot_never_flagged(make_investor):
    # A post-2018 lot is never grandfathered, so the flag is irrelevant (False).
    inv = make_investor()
    _equity_txn(inv, txn_type="buy", units="10", price="100", on=dt.date(2020, 1, 1))
    _equity_txn(inv, txn_type="sell", units="10", price="200", on=dt.date(2024, 8, 1))

    cg = build_capital_gains(inv, "2024-25", fmv_lookup=lambda *_: None)
    assert cg["rows"][0]["grandfathering_unavailable"] is False


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


def test_orphan_sell_tradebook_excluded_from_capital_gains(make_investor):
    """Mid-history tradebook with orphan sells must not fabricate STCG/LTCG."""
    inv = make_investor()
    rows = _eq_row("2024-08-01", "sell", 10, 2000) + _eq_row("2024-09-01", "buy", 5, 2100)
    _run_csv(inv, _EQ_HEADER + rows)

    cg = build_capital_gains(inv, "2024-25", fmv_lookup=lambda *_: None)
    assert cg["rows"] == []
    assert cg["stcg_total"] == Decimal("0.00")
    assert cg["ltcg_total"] == Decimal("0.00")

    s112a = build_schedule_112a(inv, "2024-25", fmv_lookup=lambda *_: None)
    assert s112a["row_count"] == 0


def test_complete_tradebook_equity_in_capital_gains(make_investor):
    """A solvent equity tradebook ledger produces realised LTCG rows."""
    inv = make_investor()
    rows = _eq_row("2020-01-01", "buy", 10, 1000) + _eq_row("2024-08-01", "sell", 10, 2000)
    _run_csv(inv, _EQ_HEADER + rows)

    cg = build_capital_gains(inv, "2024-25", fmv_lookup=lambda *_: None)
    assert len(cg["rows"]) == 1
    assert cg["rows"][0]["term"] == "long"
    assert cg["ltcg_total"] == Decimal("10000.00")
    assert cg["stcg_total"] == Decimal("0.00")

    s112a = build_schedule_112a(inv, "2024-25", fmv_lookup=lambda *_: None)
    assert s112a["row_count"] == 1


def test_pre_2018_equity_grandfathering_in_schedule_112a(make_investor):
    inv = make_investor()
    rows = _eq_row("2017-01-01", "buy", 10, 50) + _eq_row("2024-08-01", "sell", 10, 150)
    _run_csv(inv, _EQ_HEADER + rows)

    s112a = build_schedule_112a(inv, "2024-25", fmv_lookup=lambda _isin, _on: Decimal("90"))
    assert s112a["row_count"] == 1
    assert s112a["rows"][0]["Share/Unit acquired(1a)"] == "BE"
    assert s112a["rows"][0]["Cost of acquisition without indexation(7)"] == "900.00"
