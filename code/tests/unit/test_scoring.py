"""Deterministic submission-quality scoring."""
from __future__ import annotations

from app.findings.builder import FindingBuilder, FindingDraft
from app.schemas.findings import (
    ConclusionState,
    FindingClassification,
    Severity,
)
from app.schemas.review import (
    CaseStatus,
    CheckOutcome,
    CheckStatus,
    ReviewResult,
    StageResult,
    StageStatus,
)
from app.scoring import score_submission
from tests.conftest import envelope, make_case

_STAGE_NAMES = [
    "case_initialization", "document_inventory", "normalization",
    "within_document_validation", "cross_document_reconciliation",
    "external_truth_checks", "pricing_input_validation", "exposure_coverage_fit",
    "document_integrity_signals", "gpt_semantic_review",
    "dedup_severity_prioritization", "findings_synthesis",
]


def _result(stages=None, checks=None, errors=None):
    return ReviewResult(
        run_id="RUN-T", submission_id="T-SCORE", status=CaseStatus.PASS,
        stages=stages if stages is not None else [
            StageResult(stage=name, status=StageStatus.COMPLETED)
            for name in _STAGE_NAMES],
        checks=checks or [], errors=errors or [])


def _finding(severity, group_key=None):
    case = make_case([envelope("quote", {"terms": []})])
    return FindingBuilder(case).build(FindingDraft(
        finding_type="X", taxonomy_id="U01", rule_id="R", rule_version="1",
        title="t", classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
        conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
        severity=severity,
        issue_group_key=group_key or f"test_group_{severity.value.lower()}"))


def test_clean_run_scores_100_grade_a():
    result = _result()
    result.status = CaseStatus.PASS
    score = score_submission(result)
    assert score.score == 100.0 and score.grade == "A"
    assert score.components == []
    assert score.method.startswith("spectra-quality-score@")


def test_severity_weighted_penalties():
    result = _result()
    result.findings = [_finding(Severity.HIGH), _finding(Severity.MEDIUM),
                       _finding(Severity.LOW)]
    result.status = CaseStatus.REVIEW
    score = score_submission(result)
    assert score.score == 100.0 - (6 + 3 + 1)
    component = next(c for c in score.components if c.name == "issue_severity")
    assert component.points_lost == 10.0
    assert "High severity" in component.detail


def test_unsupported_documents_and_intake_errors_reduce_score():
    stages = [StageResult(stage=name, status=StageStatus.COMPLETED)
              for name in _STAGE_NAMES]
    inventory = next(s for s in stages if s.stage == "document_inventory")
    inventory.metrics = {"unsupported": 2, "documents_loaded": 4}
    inventory.errors = ["bad envelope"]
    score = score_submission(_result(stages=stages))
    intake = next(c for c in score.components if c.name == "document_completeness")
    assert intake.points_lost == 3 * 2 + 4
    assert score.score == 100 - intake.points_lost


def test_documents_without_evidence_and_failed_checks_penalized():
    stages = [StageResult(stage=name, status=StageStatus.COMPLETED)
              for name in _STAGE_NAMES]
    norm = next(s for s in stages if s.stage == "normalization")
    norm.metrics = {"documents_without_evidence": ["a.json", "b.json"],
                    "exposure_model_persisted": False}
    checks = [CheckOutcome(check_id="X", status=CheckStatus.INCOMPLETE)]
    score = score_submission(_result(stages=stages, checks=checks))
    by_name = {c.name: c.points_lost for c in score.components}
    assert by_name["evidence_coverage"] == 2 * 2 + 4
    assert by_name["check_reliability"] == 3


def test_issue_penalty_is_capped_and_grades_are_banded():
    result = _result()
    result.findings = [_finding(Severity.CRITICAL, f"test_group_{index}")
                       for index in range(10)]
    score = score_submission(result)
    component = next(c for c in score.components if c.name == "issue_severity")
    assert component.points_lost == 40.0  # capped
    assert score.score == 60.0 and score.grade == "D"


def test_scoring_is_deterministic():
    result = _result()
    result.findings = [_finding(Severity.HIGH)]
    first, second = score_submission(result), score_submission(result)
    assert first.score == second.score
    assert [c.model_dump(exclude={"detail"}) for c in first.components] == [
        c.model_dump(exclude={"detail"}) for c in second.components]
