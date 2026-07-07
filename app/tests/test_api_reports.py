"""Reports API: income view + the per-FY series, owner-scoped and read-only."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_core.models import TransactionType

pytestmark = pytest.mark.django_db


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


def test_income_via_api(client, make_investor, make_security, make_transaction):
    inv = make_investor()
    sec = make_security(security_type="equity", symbol="RELI", name="Reliance")
    _dividend(make_transaction, inv, sec, amount="150", on=dt.date(2024, 5, 1))

    resp = client.get(f"/api/investors/{inv.id}/reports/income", {"fy": "2024-25"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["received_total"] == "150.00"
    assert data["groups"][0]["kind"] == "dividend"


def test_income_malformed_fy_422(client, make_investor):
    inv = make_investor()
    resp = client.get(f"/api/investors/{inv.id}/reports/income", {"fy": "totally-bad"})
    assert resp.status_code == 422


def test_income_missing_investor_404(client):
    resp = client.get("/api/investors/999999/reports/income", {"fy": "2024-25"})
    assert resp.status_code == 404


def test_income_by_fy_via_api(client, make_investor, make_security, make_transaction):
    inv = make_investor()
    sec = make_security(security_type="equity", symbol="RELI")
    _dividend(make_transaction, inv, sec, amount="100", on=dt.date(2024, 6, 1))

    resp = client.get(f"/api/investors/{inv.id}/reports/income-by-fy")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{"fy": "2024-25", "dividends": "100.00", "interest": "0.00"}]


def test_capital_gains_by_fy_via_api(client, make_investor):
    inv = make_investor()
    resp = client.get(f"/api/investors/{inv.id}/reports/capital-gains-by-fy")
    assert resp.status_code == 200
    assert resp.json() == []  # no disposals → empty series


def test_income_csv_via_api(client, make_investor, make_security, make_transaction):
    inv = make_investor()
    sec = make_security(security_type="equity", symbol="RELI", name="Reliance")
    _dividend(make_transaction, inv, sec, amount="100", on=dt.date(2024, 5, 1))

    resp = client.get(f"/api/investors/{inv.id}/reports/income.csv", {"fy": "2024-25"})
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert b"Reliance" in resp.content
