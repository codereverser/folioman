"""Recurring-income report + the per-FY series driving the year-over-year charts."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.services.income import (
    build_income_by_fy,
    build_income_csv,
    build_income_report,
)
from folioman_app.services.tax_export import build_capital_gains_by_fy
from folioman_app.tasks.import_csv import process_csv
from folioman_core.models import TransactionType

pytestmark = pytest.mark.django_db

_ISIN = "INE002A01018"  # Reliance
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
    from folioman_app.models.jobs import ImportJob, ImportKind

    job = ImportJob.objects.create(investor=investor, kind=ImportKind.CSV)
    return process_csv(job, text.encode(), "")


def _dividend(make_transaction, inv, sec, *, amount, on):
    return make_transaction(
        investor=inv,
        security=sec,
        transaction_type=TransactionType.DIVIDEND.value,
        units=Decimal("0"),
        nav_or_price=Decimal("0"),
        amount=Decimal(amount),
        date=on,
    )


# --- build_income_report ----------------------------------------------------


def test_income_report_groups_dividends_by_security(make_investor, make_security, make_transaction):
    inv = make_investor()
    sec = make_security(security_type="equity", symbol="RELI", name="Reliance")
    # Two dividends on the same security in FY 2024-25 (Q1 + Q3) — one summed row.
    _dividend(make_transaction, inv, sec, amount="100", on=dt.date(2024, 5, 1))
    _dividend(make_transaction, inv, sec, amount="200", on=dt.date(2024, 11, 1))
    # A dividend in a different FY must not leak in.
    _dividend(make_transaction, inv, sec, amount="999", on=dt.date(2023, 6, 1))

    report = build_income_report(inv, "2024-25")
    assert len(report["groups"]) == 1
    group = report["groups"][0]
    assert group["kind"] == "dividend"
    assert group["basis"] == "received"
    assert group["received_total"] == Decimal("300")
    assert len(group["rows"]) == 1
    row = group["rows"][0]
    assert row["asset_type"] == "equity"
    assert row["accrued"] == row["received"] == Decimal("300")  # dividends: received basis
    assert report["received_total"] == report["accrued_total"] == Decimal("300")


def test_income_report_quarterly_split(make_investor, make_security, make_transaction):
    inv = make_investor()
    sec = make_security(security_type="equity", symbol="RELI")
    _dividend(make_transaction, inv, sec, amount="100", on=dt.date(2024, 5, 1))  # Upto 15/6
    _dividend(make_transaction, inv, sec, amount="200", on=dt.date(2024, 11, 1))  # 16/9 to 15/12

    quarters = {
        q["label"]: q["amount"] for q in build_income_report(inv, "2024-25")["dividend_quarters"]
    }
    assert quarters == {"Upto 15/6": Decimal("100"), "16/9 to 15/12": Decimal("200")}


def test_income_report_empty_when_no_dividends(make_investor):
    report = build_income_report(make_investor(), "2024-25")
    assert report["groups"] == []
    assert report["received_total"] == Decimal("0.00")
    assert report["dividend_quarters"] == []


def test_income_report_malformed_fy_raises(make_investor):
    with pytest.raises(ValueError):
        build_income_report(make_investor(), "not-a-fy")


# --- build_income_by_fy -----------------------------------------------------


def test_income_by_fy_totals_across_years(make_investor, make_security, make_transaction):
    inv = make_investor()
    sec = make_security(security_type="equity", symbol="RELI")
    _dividend(make_transaction, inv, sec, amount="100", on=dt.date(2023, 6, 1))
    _dividend(make_transaction, inv, sec, amount="250", on=dt.date(2024, 6, 1))

    series = build_income_by_fy(inv)
    assert [p["fy"] for p in series] == ["2023-24", "2024-25"]  # ascending in time
    assert series[0]["dividends"] == Decimal("100")
    assert series[1]["dividends"] == Decimal("250")
    assert all(p["interest"] == Decimal("0.00") for p in series)


# --- build_capital_gains_by_fy ----------------------------------------------


def test_capital_gains_by_fy_gain_and_loss_years(make_investor):
    inv = make_investor()
    # Buy 20 @100 (2020); a gain disposal in FY23-24, a loss disposal in FY24-25.
    rows = (
        _eq_row("2020-01-01", "buy", 20, 100)
        + _eq_row("2023-08-01", "sell", 10, 200)  # +1000 LTCG
        + _eq_row("2024-08-01", "sell", 10, 50)  # -500 LTCG (loss)
    )
    _run_csv(inv, _EQ_HEADER + rows)

    by_fy = {p["fy"]: p for p in build_capital_gains_by_fy(inv, fmv_lookup=lambda *_: None)}
    assert by_fy["2023-24"]["ltcg"] == Decimal("1000.00")
    assert by_fy["2024-25"]["ltcg"] == Decimal("-500.00")  # a loss year goes negative


# --- CSV export -------------------------------------------------------------


def test_income_csv_has_quarterly_summary(make_investor, make_security, make_transaction):
    inv = make_investor()
    sec = make_security(security_type="equity", symbol="RELI", name="Reliance")
    _dividend(make_transaction, inv, sec, amount="100", on=dt.date(2024, 5, 1))

    csv_text = build_income_csv(inv, "2024-25", basis="accrued")
    assert "kind,asset_type,security,quarter,amount_inr" in csv_text
    assert "Reliance" in csv_text
    assert "Upto 15/6" in csv_text
