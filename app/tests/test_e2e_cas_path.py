"""End-to-end MF CAS path over the HTTP API.

Drives the whole chain a client would: upload a CAS PDF -> the synchronous job
persists transactions -> reconcile -> Schedule 112A preview. The casparser PDF
read is mocked with a synthetic MfCasStatement (real ISINs, fake identity and
units, no PII) so the chain runs in CI without a real CAS PDF; real-PDF parsing
is covered by the local smoke test.

The statement carries one equity-oriented fund with a long-term disposal (a
112A LTCG row) and one debt fund with a long-term disposal (tax-safe, but not
112A-eligible) so the export's equity-oriented filter is exercised, not assumed.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from folioman_core.models import SecurityType, TransactionType
from folioman_core.models.cas import MfCasLineItem, MfCasSchemeBlock, MfCasStatement
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.security import Security as CoreSecurity

pytestmark = pytest.mark.django_db

_EQUITY_ISIN = "INF879O01027"  # Parag Parikh Flexi Cap — equity-oriented
_DEBT_ISIN = "INF179KA1HV6"  # a debt fund — not 112A-eligible


def _statement() -> MfCasStatement:
    equity = CoreSecurity(
        type=SecurityType.MF,
        name="Parag Parikh Flexi Cap",
        amfi_code="122639",
        isin=_EQUITY_ISIN,
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "PPFAS Mutual Fund"},
    )
    debt = CoreSecurity(
        type=SecurityType.MF,
        name="HDFC Liquid Fund",
        amfi_code="119010",
        isin=_DEBT_ISIN,
        metadata={"equity_oriented": False, "fund_type": "DEBT", "amc": "HDFC Mutual Fund"},
    )
    equity_block = MfCasSchemeBlock(
        folio=CoreFolio(folio_type="mf", number="12345/67", amc_code="PPFAS"),
        security=equity,
        closing_units="40",  # 100 buy - 60 sell
        transactions=[
            MfCasLineItem(
                date=dt.date(2020, 1, 1),
                transaction_type=TransactionType.BUY,
                units="100",
                nav="50",
                amount="5000",
            ),
            MfCasLineItem(
                date=dt.date(2024, 8, 1),
                transaction_type=TransactionType.SELL,
                units="60",
                nav="200",
                amount="12000",
            ),
        ],
    )
    debt_block = MfCasSchemeBlock(
        folio=CoreFolio(folio_type="mf", number="98765/43", amc_code="HDFC"),
        security=debt,
        closing_units="500",  # 1000 buy - 500 sell
        transactions=[
            MfCasLineItem(
                date=dt.date(2020, 2, 1),
                transaction_type=TransactionType.BUY,
                units="1000",
                nav="100",
                amount="100000",
            ),
            MfCasLineItem(
                date=dt.date(2024, 9, 1),
                transaction_type=TransactionType.SELL,
                units="500",
                nav="110",
                amount="55000",
            ),
        ],
    )
    return MfCasStatement(
        investor_name="Test Investor",
        investor_email="t@example.com",
        pan_masked="XXXXX1234X",
        statement_from=dt.date(2020, 1, 1),
        statement_to=dt.date(2025, 3, 31),
        schemes=[equity_block, debt_block],
    )


def _post(client, url, payload=None):
    return client.post(url, data=payload or {}, content_type="application/json")


def test_cas_pdf_full_chain_to_112a(client, make_investor, monkeypatch):
    import folioman_app.tasks.import_cas as mod
    from folioman_core.cas_reader import ParsedCas

    monkeypatch.setattr(mod, "read_cas", lambda _content, _password: ParsedCas(mf=_statement()))
    inv = make_investor()

    # 1) Upload the CAS PDF — the unified import auto-detects MF CAS + persists.
    upload = SimpleUploadedFile("cams.pdf", b"%PDF synthetic")
    resp = client.post(f"/api/investors/{inv.id}/imports/cas", {"file": upload, "password": "x"})
    assert resp.status_code == 201
    job = resp.json()
    assert job["status"] == "success"
    assert job["result"]["transactions_created"] == 4
    assert job["result"]["securities"] == 2

    # 2) Transactions landed (buy + sell per scheme).
    txns = client.get(f"/api/investors/{inv.id}/transactions").json()
    assert len(txns) == 4

    # 3) Reconcile — MF CAS is transaction-only, so both funds are full-history
    #    and tax-safe (no demat snapshot to disagree with).
    statuses = client.post(f"/api/investors/{inv.id}/integrity/recompute").json()
    assert len(statuses) == 2
    assert all(s["status"] == "full_history" for s in statuses)
    assert sum(1 for s in statuses if s["tax_safe"]) == 2

    # 4) Schedule 112A preview — only the equity-oriented long-term sale qualifies;
    #    the (tax-safe) debt disposal is filtered out by eligibility.
    body = _post(client, f"/api/investors/{inv.id}/exports/schedule-112a", {"fy": "2024-25"}).json()
    assert body["row_count"] == 1
    row = body["rows"][0]
    assert row["ISIN Code(2)"] == _EQUITY_ISIN
    assert row["Share/Unit acquired(1a)"] == "AE"  # acquired after 31-Jan-2018
