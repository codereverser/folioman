"""Imports API: CAS upload (PAN-resolved) + per-investor job reads, scoping, 404s."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from folioman_core.models.cas import MfCasStatement

pytestmark = pytest.mark.django_db


def _empty_cas(make_parsed_cas):
    """A parseable CAS with no schemes — enough to drive the job to success without
    asserting on imported content (these tests exercise the job/scoping plumbing)."""
    return make_parsed_cas(mf=MfCasStatement(schemes=[]))


def test_import_routes_in_openapi(client):
    paths = client.get("/api/openapi.json").json()["paths"]
    # CAS upload is advisor-level + PAN-resolved (preview, then import); CSV and the
    # job-read endpoints stay per-investor.
    assert "/api/imports/cas" in paths
    assert "/api/imports/cas/preview" in paths
    assert "/api/investors/{investor_id}/imports/csv" in paths
    assert "/api/investors/{investor_id}/imports/{job_id}" in paths


def test_upload_creates_job_and_is_retrievable(client, patch_cas, make_parsed_cas):
    patch_cas(_empty_cas(make_parsed_cas))
    upload = SimpleUploadedFile("cams.pdf", b"%PDF fake")
    resp = client.post("/api/imports/cas", {"file": upload, "password": "s"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "cas"
    assert body["filename"] == "cams.pdf"
    assert len(body["source_ref"]) == 64  # sha256 hex of the upload
    inv_id = body["investor_id"]  # investor resolved/created from the statement PAN
    # retrievable via the per-investor poll endpoint
    poll = client.get(f"/api/investors/{inv_id}/imports/{body['id']}").json()
    assert poll["id"] == body["id"]


def test_runner_records_not_implemented_for_unknown_kind(make_investor):
    # All real kinds now have processors; the not-implemented branch only fires
    # for an unregistered kind. (DB choices aren't enforced, so we can forge one.)
    from folioman_app.models import ImportJob
    from folioman_app.services.imports import run_import_job

    job = ImportJob.objects.create(investor=make_investor(), kind="unknownkind")
    run_import_job(job, content=b"x")
    assert job.status == "failed"
    assert "not implemented" in job.error.lower()


def test_pan_less_statement_is_rejected(client, patch_cas, make_parsed_cas):
    # No PAN -> we can't attribute the statement to anyone; reject, create nothing.
    from folioman_app.models import ImportJob, Investor

    patch_cas(make_parsed_cas(mf=MfCasStatement(schemes=[]), pan=""))
    upload = SimpleUploadedFile("cams.pdf", b"%PDF fake")
    resp = client.post("/api/imports/cas", {"file": upload, "password": "s"})
    assert resp.status_code == 422
    assert Investor.objects.count() == 0
    assert ImportJob.objects.count() == 0


def test_job_is_scoped_to_its_investor(client, make_investor):
    # The per-investor job-read endpoint never leaks another investor's job.
    from folioman_app.models import ImportJob
    from folioman_app.models.jobs import ImportKind

    a, b = make_investor(), make_investor()
    job = ImportJob.objects.create(investor=a, kind=ImportKind.CAS.value)
    assert client.get(f"/api/investors/{b.id}/imports/{job.id}").status_code == 404
    assert client.get(f"/api/investors/{a.id}/imports/{job.id}").status_code == 200


def test_list_jobs_for_investor(client, make_investor):
    from folioman_app.models import ImportJob
    from folioman_app.models.jobs import ImportKind

    inv = make_investor()
    ImportJob.objects.create(investor=inv, kind=ImportKind.CAS.value, filename="a.pdf")
    ImportJob.objects.create(investor=inv, kind=ImportKind.CAS.value, filename="b.pdf")
    jobs = client.get(f"/api/investors/{inv.id}/imports").json()
    assert len(jobs) == 2
    assert {j["kind"] for j in jobs} == {"cas"}


def test_csv_upload_is_disabled(client, make_investor):
    # Generic CSV import is parked until the multi-asset phase: the endpoint
    # rejects the upload and creates no job.
    inv = make_investor()
    resp = client.post(
        f"/api/investors/{inv.id}/imports/csv",
        {"file": SimpleUploadedFile("txns.csv", b"date,type,units")},
    )
    assert resp.status_code == 503
    assert client.get(f"/api/investors/{inv.id}/imports").json() == []


def test_preview_reports_new_investor_and_masks_pan(client, patch_cas, make_parsed_cas):
    patch_cas(make_parsed_cas(mf=MfCasStatement(schemes=[]), name="Asha Rao", pan="ABCDE1234F"))
    upload = SimpleUploadedFile("cams.pdf", b"%PDF fake")
    body = client.post("/api/imports/cas/preview", {"file": upload, "password": "s"}).json()
    assert body["kind"] == "mf_cas"
    assert body["investor_name"] == "Asha Rao"
    assert body["match_investor_id"] is None  # no existing investor for this PAN
    assert body["pan_masked"].endswith("234F")
    assert "ABCDE1234F" not in body["pan_masked"]  # full PAN never returned


def test_preview_matches_existing_investor_by_pan(
    client, patch_cas, make_parsed_cas, make_investor
):
    existing = make_investor(name="Asha Rao")
    existing.set_pan("ABCDE1234F")
    existing.save()
    patch_cas(make_parsed_cas(mf=MfCasStatement(schemes=[]), pan="ABCDE1234F"))
    upload = SimpleUploadedFile("cams.pdf", b"%PDF fake")
    body = client.post("/api/imports/cas/preview", {"file": upload, "password": "s"}).json()
    assert body["match_investor_id"] == existing.id
    assert body["match_investor_name"] == "Asha Rao"


def test_reimport_same_pan_attaches_to_existing_investor(
    client, patch_cas, make_parsed_cas, make_investor
):
    existing = make_investor(name="Asha Rao")
    existing.set_pan("ABCDE1234F")
    existing.save()
    patch_cas(make_parsed_cas(mf=MfCasStatement(schemes=[]), pan="ABCDE1234F"))
    upload = SimpleUploadedFile("cams.pdf", b"%PDF fake")
    body = client.post("/api/imports/cas", {"file": upload, "password": "s"}).json()
    from folioman_app.models import Investor

    assert body["investor_id"] == existing.id  # attached, not a duplicate
    assert Investor.objects.count() == 1


def test_preview_rejects_pan_less_statement(client, patch_cas, make_parsed_cas):
    patch_cas(make_parsed_cas(mf=MfCasStatement(schemes=[]), pan=""))
    upload = SimpleUploadedFile("cams.pdf", b"%PDF fake")
    resp = client.post("/api/imports/cas/preview", {"file": upload, "password": "s"})
    assert resp.status_code == 422
