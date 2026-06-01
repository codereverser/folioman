"""Imports API: upload -> job flow, per-investor scoping, 404s.

Here an upload exercises the full job
machinery and (with no processor registered) records a clear failure.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

pytestmark = pytest.mark.django_db


def _upload(client, investor_id, kind_path, *, name="file.pdf", content=b"data", password=None):
    data = {"file": SimpleUploadedFile(name, content)}
    if password is not None:
        data["password"] = password
    return client.post(f"/api/investors/{investor_id}/imports/{kind_path}", data)


def test_import_routes_in_openapi(client):
    paths = client.get("/api/openapi.json").json()["paths"]
    # One unified CAS upload (auto-detects MF CAS vs eCAS); plus CSV.
    assert "/api/investors/{investor_id}/imports/cas" in paths
    assert "/api/investors/{investor_id}/imports/csv" in paths
    assert "/api/investors/{investor_id}/imports/{job_id}" in paths


def test_upload_creates_job_and_is_retrievable(client, make_investor):
    inv = make_investor()
    resp = _upload(client, inv.id, "cas", name="cams.pdf", content=b"%PDF fake", password="s")
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "cas"
    assert body["filename"] == "cams.pdf"
    assert body["investor_id"] == inv.id
    assert len(body["source_ref"]) == 64  # sha256 hex of the upload
    # retrievable via the poll endpoint
    assert client.get(f"/api/investors/{inv.id}/imports/{body['id']}").json()["id"] == body["id"]


def test_runner_records_not_implemented_for_unknown_kind(make_investor):
    # All real kinds now have processors; the not-implemented branch only fires
    # for an unregistered kind. (DB choices aren't enforced, so we can forge one.)
    from folioman_app.models import ImportJob
    from folioman_app.services.imports import run_import_job

    job = ImportJob.objects.create(investor=make_investor(), kind="unknownkind")
    run_import_job(job, content=b"x")
    assert job.status == "failed"
    assert "not implemented" in job.error.lower()


def test_upload_to_missing_investor_404(client):
    assert _upload(client, 999999, "cas").status_code == 404


def test_job_is_scoped_to_its_investor(client, make_investor):
    a = make_investor()
    b = make_investor()
    job_id = _upload(client, a.id, "cas").json()["id"]
    # the job belongs to A; requesting it under B 404s
    assert client.get(f"/api/investors/{b.id}/imports/{job_id}").status_code == 404
    assert client.get(f"/api/investors/{a.id}/imports/{job_id}").status_code == 200


def test_list_jobs_for_investor(client, make_investor):
    inv = make_investor()
    _upload(client, inv.id, "cas", name="a.pdf", password="p")
    _upload(client, inv.id, "cas", name="b.pdf", content=b"%PDF", password="p")
    jobs = client.get(f"/api/investors/{inv.id}/imports").json()
    assert len(jobs) == 2
    assert {j["kind"] for j in jobs} == {"cas"}


def test_csv_upload_is_disabled(client, make_investor):
    # Generic CSV import is parked until the multi-asset phase: the endpoint
    # rejects the upload and creates no job.
    inv = make_investor()
    resp = _upload(client, inv.id, "csv", name="txns.csv", content=b"date,type,units")
    assert resp.status_code == 503
    assert client.get(f"/api/investors/{inv.id}/imports").json() == []
