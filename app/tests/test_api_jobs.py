"""Jobs & valuation-status overview: advisor-wide imports + the real per-security
cause behind a valuation error. Scoped to the authenticated advisor."""

from __future__ import annotations

import datetime as dt

import pytest
from folioman_app.models import ImportJob, Investor, NAVHistory, ValuationStatus
from folioman_app.models.jobs import ImportJobStatus, ImportKind
from folioman_core.models import SecurityType

pytestmark = pytest.mark.django_db


def test_jobs_route_in_openapi(client):
    assert "/api/jobs" in client.get("/api/openapi.json").json()["paths"]


def test_overview_lists_recent_imports_newest_first(client, make_investor):
    inv = make_investor(name="Asha")
    ImportJob.objects.create(
        investor=inv, kind=ImportKind.CAS, status=ImportJobStatus.SUCCESS, filename="old.pdf"
    )
    ImportJob.objects.create(
        investor=inv, kind=ImportKind.CAS, status=ImportJobStatus.FAILED, filename="new.pdf"
    )

    body = client.get("/api/jobs").json()

    assert [j["filename"] for j in body["imports"]] == ["new.pdf", "old.pdf"]  # -created_at
    assert body["imports"][0]["investor_name"] == "Asha"
    assert body["imports"][0]["status"] == "failed"


def test_overview_classifies_real_valuation_causes(
    client, make_investor, make_security, make_folio, make_transaction
):
    """The panel surfaces the actionable per-security cause, not 'feed pending'."""
    inv = make_investor()
    mf = SecurityType.MF.value
    closed = make_security(security_type=mf, nav_feed_closed=True, name="Matured")
    unmapped = make_security(security_type=mf, amfi_code="", isin="INF0000UNP12", name="NoCode")
    pending = make_security(security_type=mf, name="Awaiting")  # has amfi_code, no NAV
    priced = make_security(security_type=mf, name="Priced")
    folio = make_folio(investor=inv)
    for sec in (closed, unmapped, pending, priced):
        make_transaction(
            investor=inv,
            security=sec,
            folio=folio,
            date=dt.date(2025, 1, 1),
            units=10,
            nav_or_price=10,
        )
    NAVHistory.objects.create(security=priced, date=dt.date(2025, 1, 1), nav=10)
    inv.valuation_status = ValuationStatus.ERROR
    inv.valuation_error = "1 securities awaiting NAV (feed pending)"
    inv.save()

    valuations = client.get("/api/jobs").json()["valuations"]
    diag = next(d for d in valuations if d["investor_id"] == inv.id)

    assert diag["status"] == "error"
    causes = {i["security_name"]: i["cause"] for i in diag["issues"]}
    assert causes == {"Matured": "closed", "NoCode": "unmapped", "Awaiting": "feed_pending"}
    assert "Priced" not in causes  # priced fund isn't an issue
    assert all(i["detail"] for i in diag["issues"])  # every cause carries a human explanation


def test_overview_is_scoped_to_the_advisor(client, make_investor, django_user_model):
    """Another advisor's investors and jobs never leak into the overview."""
    mine = make_investor(name="Mine")
    ImportJob.objects.create(investor=mine, kind=ImportKind.CAS, status=ImportJobStatus.SUCCESS)
    other_user = django_user_model.objects.create(username="other")
    theirs = Investor.objects.create(owned_by=other_user, name="Theirs")
    ImportJob.objects.create(investor=theirs, kind=ImportKind.CAS, status=ImportJobStatus.SUCCESS)

    body = client.get("/api/jobs").json()

    assert {d["investor_name"] for d in body["valuations"]} == {"Mine"}
    assert all(j["investor_id"] == mine.id for j in body["imports"])
