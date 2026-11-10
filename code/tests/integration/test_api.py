"""API integration tests via FastAPI TestClient (httpx)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture()
def client(settings, repository):
    return TestClient(create_app(settings=settings, repository=repository))


def _create_review(client, account="a01", sub="SPX-A01-2026-0101"):
    docs = []
    for path in sorted((ROOT / "samples" / account).glob("*.json")):
        docs.append({"content": json.loads(path.read_text(encoding="utf-8"))})
    payload = {
        "submission_id": sub,
        "documents": docs,
        "line_of_business": "package",
        "effective_date": "2026-11-15",
        "review_date": "2026-10-28",
        "deterministic_only": True,
    }
    res = client.post("/api/v1/reviews", json=payload)
    assert res.status_code == 201, res.text
    return res.json()


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json()["ready"] is True
    assert "api_key" not in ready.text.lower()


def test_full_review_cycle(client):
    created = _create_review(client)
    run_id = created["run_id"]
    assert created["status"] == "REVIEW"

    res = client.get(f"/api/v1/reviews/{run_id}")
    assert res.status_code == 200
    assert res.json()["submission_id"] == "SPX-A01-2026-0101"

    res = client.get(f"/api/v1/reviews/{run_id}/findings", params={"taxonomy": "U04"})
    data = res.json()
    assert data["total"] == 1
    finding = data["findings"][0]

    res = client.get(f"/api/v1/reviews/{run_id}/findings/{finding['finding_id']}")
    assert res.status_code == 200
    assert res.json()["evidence"]

    # disposition
    res = client.post(
        f"/api/v1/reviews/{run_id}/findings/{finding['finding_id']}/disposition",
        json={"action": "CONFIRM", "reviewer_id": "uw-1",
              "rationale": "verified against bureau worksheet"})
    assert res.status_code == 200
    after = client.get(f"/api/v1/reviews/{run_id}/findings/{finding['finding_id']}").json()
    assert after["status"] == "CONFIRMED"
    assert after["disposition_history"]


def test_disposition_requires_rationale(client):
    created = _create_review(client)
    run_id = created["run_id"]
    finding = created["findings"][0]
    res = client.post(
        f"/api/v1/reviews/{run_id}/findings/{finding['finding_id']}/disposition",
        json={"action": "REJECT_FALSE_POSITIVE", "reviewer_id": "uw-1", "rationale": ""})
    assert res.status_code == 422


def test_export_formats(client):
    created = _create_review(client)
    run_id = created["run_id"]
    for fmt in ("json", "csv", "markdown"):
        res = client.get(f"/api/v1/reviews/{run_id}/export", params={"format": fmt})
        assert res.status_code == 200
        assert "SPX-A01" in res.text or "finding_id" in res.text
    md = client.get(f"/api/v1/reviews/{run_id}/export",
                    params={"format": "markdown"}).text
    assert "Evidence" in md and "Recommended action" in md


def test_404s(client):
    assert client.get("/api/v1/reviews/RUN-NOPE").status_code == 404
    assert client.get("/api/v1/reviews/RUN-NOPE/findings/F-X").status_code == 404


def test_auth_token_enforced_when_configured(settings, repository):
    settings.spectra_api_token = "s3cret-token"
    client = TestClient(create_app(settings=settings, repository=repository))
    assert client.get("/api/v1/reviews").status_code == 401
    ok = client.get("/api/v1/reviews",
                    headers={"Authorization": "Bearer s3cret-token"})
    assert ok.status_code == 200
    bad = client.get("/api/v1/reviews",
                     headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401


@pytest.fixture()
def accounts_client(settings, repository, tmp_path):
    accounts_dir = tmp_path / "accounts"
    account = accounts_dir / "TEST-ACCT"
    account.mkdir(parents=True)
    (account / "loss_run.json").write_text(json.dumps({
        "schema_version": "1.0", "submission_id": "TEST-ACCT",
        "document": {"document_id": "D1", "document_type": "loss_run",
                     "document_name": "lr.json"},
        "data": {
            "valuation_date": "2026-10-01",
            "claims": [{"claim_id": "C1", "line_of_business": "GL",
                        "loss_date": "2025-01-01", "paid": 10, "reserves": 5,
                        "incurred": 15, "status": "open", "policy_year": "2025"}],
            "summary": {"total_incurred": 15, "claim_count": 1,
                        "annual_counts": [{"year": "2025", "count": 1}]},
        }}), encoding="utf-8")
    (accounts_dir / "EMPTY-ACCT").mkdir()
    settings.spectra_accounts_dir = accounts_dir
    return TestClient(create_app(settings=settings, repository=repository))


def test_accounts_listing_and_quality_score(accounts_client):
    res = accounts_client.get("/api/v1/accounts")
    assert res.status_code == 200
    accounts = {a["account"]: a for a in res.json()["accounts"]}
    assert "TEST-ACCT" in accounts
    assert accounts["TEST-ACCT"]["document_count"] == 1
    assert accounts["TEST-ACCT"]["latest_run"] is None
    assert "EMPTY-ACCT" not in accounts  # folders without JSON are not accounts


def test_account_run_progress_score_and_override(accounts_client):
    assert accounts_client.get("/api/v1/runs/RUN-NOPE/progress").status_code == 404
    assert accounts_client.post("/api/v1/accounts/NOPE/run").status_code == 404

    first = accounts_client.post("/api/v1/accounts/TEST-ACCT/run")
    assert first.status_code == 202, first.text
    first_id = first.json()["run_id"]
    # TestClient executes background tasks before returning: run is done.
    progress = accounts_client.get(f"/api/v1/runs/{first_id}/progress").json()
    assert progress["state"] == "COMPLETED" and progress["percent"] == 100.0
    assert progress["account"] == "TEST-ACCT"

    result = accounts_client.get(f"/api/v1/reviews/{first_id}").json()
    score = result["quality_score"]
    assert 0 <= score["score"] <= 100
    assert score["grade"] in list("ABCDF")
    assert score["method"].startswith("spectra-quality-score@")

    listing = accounts_client.get("/api/v1/accounts").json()["accounts"]
    latest = next(a for a in listing if a["account"] == "TEST-ACCT")["latest_run"]
    assert latest["run_id"] == first_id
    assert latest["quality_score"]["score"] == score["score"]

    second = accounts_client.post("/api/v1/accounts/TEST-ACCT/run")
    assert second.status_code == 202
    second_id = second.json()["run_id"]
    assert second_id != first_id
    runs = accounts_client.get(
        "/api/v1/reviews", params={"submission_id": "TEST-ACCT"}).json()["runs"]
    assert [r["run_id"] for r in runs] == [second_id]  # prior run overridden


def test_ui_pages_render(client):
    created = _create_review(client)
    run_id = created["run_id"]
    assert "Spectra" in client.get("/").text
    run_page = client.get(f"/ui/runs/{run_id}", follow_redirects=False)
    assert run_page.status_code == 307
    assert run_page.headers["location"].startswith("/#run/")
    fid = created["findings"][0]["finding_id"]
    page = client.get(
        f"/ui/runs/{run_id}/findings/{fid}", follow_redirects=False)
    assert page.status_code == 307
    assert f"/finding/{fid}" in page.headers["location"]
