"""Shared test fixtures. No test ever requires a real OpenAI key."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.loader import build_case, parse_envelope
from app.agents import Orchestrator
from app.config import Settings
from app.schemas.review import ReviewRequest
from app.storage import CaseRepository

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/spectra-test.db",
        spectra_data_dir=tmp_path,
        spectra_ruleset_dir=ROOT / "rulesets",
        spectra_profile_dir=ROOT / "profiles",
        spectra_llm_enabled=False,
    )


@pytest.fixture()
def repository(settings) -> CaseRepository:
    return CaseRepository(settings.sqlite_path)


def make_case(documents: list[dict], submission_id: str = "T-001", **kwargs):
    loaded = [parse_envelope(d, f"<test:{i}>") for i, d in enumerate(documents)]
    return build_case(submission_id, "RUN-T", loaded, **kwargs)


def envelope(doc_type: str, data: dict, doc_id: str | None = None,
             version: str = "received", **meta) -> dict:
    return {
        "schema_version": "1.0",
        "submission_id": "T-001",
        "document": {
            "document_id": doc_id or f"T-{doc_type}",
            "document_type": doc_type,
            "document_name": meta.pop("document_name", f"{doc_type}.json"),
            "version": version,
            "received_at": meta.pop("received_at", "2026-10-20T10:00:00Z"),
            **meta,
        },
        "data": data,
        "provenance": {"extraction_source": "unit_test", "source_file": f"{doc_type}.json",
                       "fields": meta.pop("fields", {})},
    }


def run_samples_review(settings, repository, account: str, submission_id: str):
    orchestrator = Orchestrator(settings=settings, repository=repository)
    request = ReviewRequest(
        submission_id=submission_id,
        input_dir=str(ROOT / "samples" / account),
        line_of_business="package",
        effective_date="2026-11-15",
        review_date="2026-10-28",
        deterministic_only=True,
    )
    return orchestrator.run_review(request)


@pytest.fixture()
def review_runner(settings, repository):
    def _run(account: str, submission_id: str):
        return run_samples_review(settings, repository, account, submission_id)
    return _run
