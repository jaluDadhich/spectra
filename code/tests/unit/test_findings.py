"""Finding builder validation, dedup, and case-status precedence."""
from __future__ import annotations

from datetime import datetime, timezone

from app.findings import compute_case_status, deduplicate
from app.findings.category_status import derive_category_assessments
from app.findings.builder import FindingBuilder, FindingDraft
from app.catalog.issue_register import load_register
from app.schemas.findings import CategoryStatus
from app.schemas.findings import (
    CaseStatus,
    ConclusionState,
    FindingClassification,
    FindingStatus,
    Severity,
)
from app.schemas.review import CheckOutcome, CheckStatus
from tests.conftest import envelope, make_case


def _draft(**kw):
    base = dict(
        finding_type="X", taxonomy_id="P5", rule_id="R", rule_version="1",
        title="t", classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
        conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
        deduplication_key="k1")
    base.update(kw)
    return FindingDraft(**base)


def _finding(with_evidence: bool = False, **kw) -> object:
    case = make_case([envelope("quote", {"terms": [
        {"term_type": "limit", "coverage": "cargo", "value": 100}]})])
    if with_evidence:
        kw.setdefault("evidence_ids", [next(iter(case.evidence_store))])
    return FindingBuilder(case).build(_draft(**kw))


def test_builder_rejects_nonexistent_evidence():
    case = make_case([envelope("quote", {"terms": []})])
    builder = FindingBuilder(case)
    assert builder.build(_draft(evidence_ids=["EV-NOPE-1"])) is None


def test_builder_accepts_and_resolves_evidence():
    case = make_case([envelope("quote", {"terms": [
        {"term_type": "limit", "coverage": "cargo", "value": 100}]})])
    eid = next(iter(case.evidence_store))
    finding = FindingBuilder(case).build(_draft(evidence_ids=[eid]))
    assert finding is not None
    assert finding.evidence[0].evidence_id == eid
    assert finding.evidence[0].document_id == "T-quote"


def test_stable_ids_are_deterministic():
    f1 = _finding()
    f2 = _finding()
    assert f1.finding_id == f2.finding_id


def test_dedup_merges_same_key_keeps_highest_severity():
    now = datetime.now(timezone.utc).isoformat()
    f1 = _finding(severity=Severity.HIGH)
    f2 = _finding(severity=Severity.LOW)
    f2.finding_id = "F-DIFFERENT"
    f2.created_at = f2.updated_at = now
    merged = deduplicate([f1, f2])
    assert len(merged) == 1
    assert merged[0].severity == Severity.HIGH
    assert "F-DIFFERENT" in merged[0].related_finding_ids


def test_dedup_p5_covers_n5_when_evidence_subset():
    p5 = _finding(with_evidence=True, taxonomy_id="P5", deduplication_key="P5:x")
    n5 = _finding(with_evidence=True, taxonomy_id="N5", deduplication_key="N5:x")
    n5.evidence = list(p5.evidence)  # same underlying evidence
    merged = deduplicate([p5, n5])
    assert len(merged) == 1
    assert merged[0].taxonomy_id == "P5"


def test_dedup_preserves_independent_n5():
    p5 = _finding(taxonomy_id="P5", deduplication_key="P5:x")
    n5 = _finding(taxonomy_id="N5", deduplication_key="N5:y")
    n5.evidence = []  # no shared evidence
    merged = deduplicate([p5, n5])
    assert {f.taxonomy_id for f in merged} == {"P5", "N5"}


def test_dedup_does_not_merge_independent_findings_in_one_unified_category():
    first = _finding(with_evidence=True, taxonomy_id="U08", deduplication_key="U08:a")
    second = _finding(with_evidence=True, taxonomy_id="U08", deduplication_key="U08:b")
    second.evidence = list(first.evidence)
    merged = deduplicate([first, second])
    assert len(merged) == 2


def test_category_is_not_clean_when_a_mapped_rule_was_skipped():
    case = make_case([envelope("fleet_schedule", {"vehicles": [
        {"unit_id": "V1", "status": "active"},
    ]})])
    register = load_register()
    category = next(item for item in register.issues if item.id == "U12")
    checks = [
        CheckOutcome(
            check_id=rule_id,
            status=(CheckStatus.COMPLETED if index == 0
                    else CheckStatus.SKIPPED_NOT_APPLICABLE))
        for index, rule_id in enumerate(category.rule_ids)
    ]

    assessments = derive_category_assessments(
        case, register, [], checks, semantic_review_completed=False)
    driver = next(item for item in assessments if item.issue_id == "U12")
    assert driver.status == CategoryStatus.NOT_ASSESSABLE


def _ok_check():
    return CheckOutcome(check_id="c", status=CheckStatus.COMPLETED)


def test_status_precedence():
    f_open = _finding()
    assert compute_case_status([], [_ok_check()]) == CaseStatus.PASS
    assert compute_case_status([f_open], [_ok_check()]) == CaseStatus.REVIEW
    f_block = _finding(blocking=True)
    assert compute_case_status([f_block], [_ok_check()]) == CaseStatus.HOLD
    bad_check = CheckOutcome(check_id="c", status=CheckStatus.INCOMPLETE)
    assert compute_case_status([], [bad_check]) == CaseStatus.ERROR_INCOMPLETE
    assert compute_case_status([f_block], [bad_check]) == CaseStatus.ERROR_INCOMPLETE
    assert compute_case_status([], [_ok_check()], run_errors=["x"]) == CaseStatus.ERROR_INCOMPLETE


def test_resolved_findings_do_not_hold_case():
    f = _finding(blocking=True)
    f.status = FindingStatus.RESOLVED
    assert compute_case_status([f], [_ok_check()]) == CaseStatus.PASS


def test_confidence_not_fabricated():
    f = _finding()
    assert f.confidence.value is None
    assert f.confidence.calibrated is False
