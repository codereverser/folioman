"""Import quarantine: a row/block that can't persist is set aside, not silently
dropped or allowed to abort the whole import. Covers per-row diversion (MF CAS +
eCAS), the job runner recording + auto-resolve on a corrected re-import, and the
list/dismiss API."""

from __future__ import annotations

import datetime as dt

import pytest
from folioman_app.models import ImportJob, ImportQuarantine, Security, Transaction
from folioman_app.models.jobs import ImportJobStatus, ImportKind
from folioman_app.services.imports import resolve_quarantine, run_import_job
from folioman_app.tasks import import_cas, import_ecas
from folioman_app.tasks._upsert import upsert_security as _real_upsert_security
from folioman_app.tasks.import_cas import persist_mf_statement
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_core.models import Depository, SecurityType, TransactionType
from folioman_core.models.cas import (
    EcasAccountBlock,
    EcasHoldingLine,
    EcasStatement,
    MfCasLineItem,
    MfCasSchemeBlock,
    MfCasStatement,
)
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.security import Security as CoreSecurity

pytestmark = pytest.mark.django_db

_GOOD_ISIN = "INF879O01027"  # Parag Parikh Flexi Cap
_BAD_ISIN = "INF179K01XQ0"  # the scheme we force to fail
_BAD_FOLIO = "999/11"


def _mf_block(isin: str, folio: str, *, units: str = "100", nav: str = "75") -> MfCasSchemeBlock:
    return MfCasSchemeBlock(
        folio=CoreFolio(folio_type="mf", number=folio, amc_code="X"),
        security=CoreSecurity(type=SecurityType.MF, name=f"Fund {isin}", isin=isin),
        closing_units=units,
        transactions=[
            MfCasLineItem(
                date=dt.date(2024, 1, 1),
                transaction_type=TransactionType.BUY,
                units=units,
                nav=nav,
                amount="7500",
            )
        ],
    )


def _two_scheme_statement() -> MfCasStatement:
    return MfCasStatement(
        investor_name="Test Investor",
        statement_from=dt.date(2024, 1, 1),
        statement_to=dt.date(2024, 12, 31),
        schemes=[_mf_block(_GOOD_ISIN, "111/1"), _mf_block(_BAD_ISIN, _BAD_FOLIO)],
    )


def _fail_isin(monkeypatch, module, bad_isin: str) -> None:
    """Make ``module.upsert_security`` raise for ``bad_isin``, real otherwise."""

    def _fake(core_security, **kwargs):
        if core_security.isin == bad_isin:
            msg = f"unmappable security {bad_isin}"
            raise ValueError(msg)
        return _real_upsert_security(core_security, **kwargs)

    monkeypatch.setattr(module, "upsert_security", _fake)


# --- per-row diversion --------------------------------------------------------


def test_mf_cas_bad_block_is_quarantined_good_block_commits(make_investor, monkeypatch):
    inv = make_investor()
    _fail_isin(monkeypatch, import_cas, _BAD_ISIN)

    summary = persist_mf_statement(inv, _two_scheme_statement(), source_ref="r1")

    # Good scheme persisted; bad one set aside — the import wasn't aborted wholesale.
    assert Transaction.objects.filter(investor=inv, security__isin=_GOOD_ISIN).exists()
    assert not Security.objects.filter(isin=_BAD_ISIN).exists()
    assert summary["schemes"] == 1  # only the one that actually persisted
    assert len(summary["quarantined"]) == 1
    bad = summary["quarantined"][0]
    assert bad["isin"] == _BAD_ISIN
    assert bad["folio"] == _BAD_FOLIO
    assert "unmappable" in bad["reason"]


def test_ecas_bad_line_is_quarantined_good_line_commits(make_investor, monkeypatch):
    inv = make_investor()
    good = CoreSecurity(type=SecurityType.EQUITY, name="Reliance", isin="INE002A01018")
    bad = CoreSecurity(type=SecurityType.BOND, name="REC Bond", isin="INE020B08DG9")
    statement = EcasStatement(
        depository=Depository.CDSL,
        statement_date=dt.date(2025, 6, 1),
        accounts=[
            EcasAccountBlock(
                folio=CoreFolio(folio_type="demat", number="1208160001234567", broker="ZERODHA"),
                holdings=[
                    EcasHoldingLine(security=good, units="10", value_observed="28500"),
                    EcasHoldingLine(security=bad, units="5", value_observed="50000"),
                ],
            )
        ],
    )
    _fail_isin(monkeypatch, import_ecas, "INE020B08DG9")

    summary = persist_ecas_statement(inv, statement, source_ref="r1")

    assert inv.holdings.filter(security__isin="INE002A01018").exists()
    assert not Security.objects.filter(isin="INE020B08DG9").exists()
    assert summary["holdings_created"] == 1
    assert len(summary["quarantined"]) == 1
    assert summary["quarantined"][0]["isin"] == "INE020B08DG9"


# --- job runner: record + status + auto-resolve -------------------------------


def _run_cas(inv, statement, make_parsed_cas) -> ImportJob:
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CAS, filename="cas.pdf")
    parsed = make_parsed_cas(mf=statement)
    return run_import_job(job, content=b"%PDF", password="", parsed=parsed)


def test_run_import_job_records_quarantine_and_flags_warnings(
    make_investor, make_parsed_cas, monkeypatch
):
    inv = make_investor()
    _fail_isin(monkeypatch, import_cas, _BAD_ISIN)

    job = _run_cas(inv, _two_scheme_statement(), make_parsed_cas)

    # Data landed (good scheme), so it's a warning, not a failure.
    assert job.status == ImportJobStatus.COMPLETED_WITH_WARNINGS
    rows = ImportQuarantine.objects.filter(investor=inv, resolved=False)
    assert rows.count() == 1
    row = rows.get()
    assert row.isin == _BAD_ISIN and row.folio_number == _BAD_FOLIO
    assert row.import_job_id == job.id
    assert "unmappable" in row.reason


def test_corrected_reimport_auto_resolves_quarantine(make_investor, make_parsed_cas, monkeypatch):
    inv = make_investor()
    _fail_isin(monkeypatch, import_cas, _BAD_ISIN)
    _run_cas(inv, _two_scheme_statement(), make_parsed_cas)
    assert ImportQuarantine.objects.filter(investor=inv, resolved=False).count() == 1

    # Re-import a corrected statement where the previously-bad scheme now persists.
    monkeypatch.undo()  # stop forcing the failure
    corrected = MfCasStatement(
        investor_name="Test Investor",
        statement_from=dt.date(2024, 1, 1),
        statement_to=dt.date(2024, 12, 31),
        schemes=[_mf_block(_BAD_ISIN, _BAD_FOLIO)],
    )
    _run_cas(inv, corrected, make_parsed_cas)

    # The scheme now has a ledger, so its quarantine row auto-resolves.
    assert Transaction.objects.filter(investor=inv, security__isin=_BAD_ISIN).exists()
    assert ImportQuarantine.objects.filter(investor=inv, resolved=False).count() == 0
    assert ImportQuarantine.objects.filter(investor=inv, resolved=True).count() == 1


def test_resolve_quarantine_leaves_unfixed_rows_open(make_investor, make_transaction):
    inv = make_investor()
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CAS)
    # One row whose security/folio never got data → stays open.
    ImportQuarantine.objects.create(
        investor=inv, import_job=job, isin="INF000X00099", folio_number="ZZZ", reason="boom"
    )

    assert resolve_quarantine(inv) == 0
    assert ImportQuarantine.objects.filter(investor=inv, resolved=False).count() == 1


# --- API: list + dismiss ------------------------------------------------------


def _quarantine_row(inv, **kw) -> ImportQuarantine:
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CAS)
    return ImportQuarantine.objects.create(investor=inv, import_job=job, **kw)


def test_quarantine_list_endpoint_returns_open_rows(client, make_investor):
    inv = make_investor()
    _quarantine_row(inv, security_name="Fund A", isin="INF000X00001", reason="bad row")
    _quarantine_row(inv, security_name="Fund B", isin="INF000X00002", resolved=True)  # hidden

    resp = client.get(f"/api/investors/{inv.id}/imports/quarantine")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["security_name"] == "Fund A"
    assert body[0]["reason"] == "bad row"


def test_quarantine_dismiss_marks_resolved(client, make_investor):
    inv = make_investor()
    row = _quarantine_row(inv, security_name="Fund A", isin="INF000X00001", reason="bad")

    resp = client.delete(f"/api/investors/{inv.id}/imports/quarantine/{row.id}")

    assert resp.status_code == 204
    row.refresh_from_db()
    assert row.resolved is True and row.resolved_at is not None
    # Gone from the open list.
    assert client.get(f"/api/investors/{inv.id}/imports/quarantine").json() == []


def test_quarantine_scoped_to_owner(client, make_investor):
    inv = make_investor()
    row = _quarantine_row(inv, isin="INF000X00001", reason="bad")
    # A bogus investor id 404s (ownership enforced by get_owned_investor).
    assert client.get("/api/investors/999999/imports/quarantine").status_code == 404
    assert client.delete(f"/api/investors/999999/imports/quarantine/{row.id}").status_code == 404
