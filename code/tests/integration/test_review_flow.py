"""Integration: full deterministic reviews over the synthetic submissions."""
from __future__ import annotations

from app.schemas.findings import CaseStatus


def test_clean_submission_passes(review_runner):
    result = review_runner("clean", "SPX-CLEAN-2026-0001")
    assert result.status == CaseStatus.PASS, [
        (finding.taxonomy_id, finding.finding_type, finding.title)
        for finding in result.findings
    ]
    assert result.findings == []
    assert all(c.status.value != "FAILED" for c in result.checks)


def test_a01_discovers_expected_taxonomies(review_runner):
    result = review_runner("a01", "SPX-A01-2026-0101")
    assert result.status == CaseStatus.REVIEW
    tax = {f.taxonomy_id for f in result.findings}
    assert {"U01", "U03", "U04", "U05", "U07", "U08",
            "U09", "U11", "U12", "U13"} <= tax
    assert all(taxonomy in {f"U{n:02d}" for n in range(1, 15)} for taxonomy in tax)
    for absent in ("U02", "U10", "U14"):
        assert absent not in tax
    assert "U06" not in tax  # sample's aligned location/class counts reconcile


def test_a02_p4_p5_detected_n5_absent(review_runner):
    result = review_runner("a02", "SPX-A02-2026-0202")
    tax = {f.taxonomy_id for f in result.findings}
    assert {"U01", "U02", "U05", "U07", "U08", "U09",
            "U11", "U12", "U13"} <= tax
    annual_count = next(
        f for f in result.findings
        if f.finding_type == "ANNUAL_CLAIM_COUNT_MISMATCH")
    assert annual_count.taxonomy_id == "U08"
    assert annual_count.evidence  # explicit GL scope is compared to GL loss detail
    assert "U03" not in tax and "U10" not in tax
    assert "U06" not in tax  # no aligned employee-count mismatch in this sample


def test_a03_p1_p11_detected_n14_absent(review_runner):
    result = review_runner("a03", "SPX-A03-2026-1042")
    tax = {f.taxonomy_id for f in result.findings}
    assert {"U03", "U04", "U05", "U07", "U08", "U09",
            "U10", "U11", "U12", "U13", "U14"} <= tax
    assert all(taxonomy.startswith("U") for taxonomy in tax)
    assert "U06" not in tax  # sample's aligned location/class counts reconcile


def test_findings_have_real_evidence_and_no_fabricated_citations(review_runner):
    result = review_runner("a01", "SPX-A01-2026-0101")
    for f in result.findings:
        for e in f.evidence:
            assert e.evidence_id.startswith("EV-")
            # page/cell only when the fixture actually supplied provenance
            if e.source_location.available:
                assert e.source_location.sheet or e.source_location.page


def test_rerun_is_idempotent(review_runner):
    r1 = review_runner("a01", "SPX-A01-2026-0101")
    r2 = review_runner("a01", "SPX-A01-2026-0101")
    assert r1.run_id != r2.run_id  # history preserved
    assert [f.finding_id for f in r1.findings] == [f.finding_id for f in r2.findings]


def test_run_persisted_with_stages(review_runner, repository):
    result = review_runner("a01", "SPX-A01-2026-0101")
    loaded = repository.get_run(result.run_id)
    assert loaded is not None
    assert loaded.status == result.status
    stage_names = {s.stage: s.status.value for s in loaded.stages}
    assert stage_names["cross_document_reconciliation"] == "COMPLETED"
    assert stage_names["external_truth_checks"] == "SKIPPED"
    trail = repository.audit_trail(result.run_id)
    assert any(e["action"] == "review_completed" for e in trail)


def test_deterministic_only_marks_semantic_skipped(review_runner):
    result = review_runner("a01", "SPX-A01-2026-0101")
    stage9 = next(s for s in result.stages if s.stage == "gpt_semantic_review")
    assert stage9.status.value == "SKIPPED"
    assert result.token_usage.calls == 0


def test_category_assessments_cover_register(review_runner):
    result = review_runner("a01", "SPX-A01-2026-0101")
    assert [assessment.issue_id for assessment in result.category_assessments] == [
        f"U{number:02d}" for number in range(1, 15)]


def test_quality_score_computed_for_every_run(review_runner):
    clean = review_runner("clean", "SPX-CLEAN-2026-0001")
    assert clean.quality_score is not None
    assert clean.quality_score.score == 100.0
    assert clean.quality_score.grade == "A"

    a01 = review_runner("a01", "SPX-A01-2026-0101")
    assert a01.quality_score is not None
    assert 0 <= a01.quality_score.score < 100
    assert any(c.name == "issue_severity" for c in a01.quality_score.components)
