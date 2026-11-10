"""Account-agnostic checks against the supplied, non-synthetic JSON packets."""
from __future__ import annotations

import pytest

from app.agents import Orchestrator
from app.schemas.findings import CategoryStatus, ConclusionState
from app.schemas.review import ReviewRequest
from tests.conftest import ROOT

DOCUMENTS = ROOT.parent / "documents"
PACKETS = [
    "A01_Walmart_Inc",
    "A02_Costco_Wholesale_Corporation",
    "A03_FedEx_Corporation",
    "A04_Cleaned_Packet",
]


@pytest.mark.parametrize("packet", PACKETS)
def test_supplied_json_packet_has_exclusive_taxonomy_and_all_category_statuses(
        packet, settings, repository):
    folder = DOCUMENTS / packet
    if not folder.is_dir():
        pytest.skip(f"Optional supplied packet is absent: {packet}")
    json_files = list(folder.glob("*.json"))
    if not json_files:
        pytest.skip(f"No JSON documents in supplied packet: {packet}")

    orchestrator = Orchestrator(settings=settings, repository=repository)
    result = orchestrator.run_review(ReviewRequest(
        submission_id=f"SUPPLIED-{packet}",
        input_dir=str(folder),
        line_of_business="package",
        effective_date="2026-11-15",
        review_date="2026-10-28",
        deterministic_only=True,
    ))

    assert {finding.taxonomy_id for finding in result.findings} <= {
        f"U{number:02d}" for number in range(1, 15)}
    assert [item.issue_id for item in result.category_assessments] == [
        f"U{number:02d}" for number in range(1, 15)]
    assert all(item.status in CategoryStatus for item in result.category_assessments)
    assert all(
        finding.evidence
        for finding in result.findings
        if finding.conclusion_state != ConclusionState.EVIDENCE_GAP
    ), [
        (finding.taxonomy_id, finding.rule_id, finding.title,
         finding.conclusion_state.value)
        for finding in result.findings
        if not finding.evidence
        and finding.conclusion_state != ConclusionState.EVIDENCE_GAP
    ]
    stage_one = next(stage for stage in result.stages if stage.stage == "document_inventory")
    stage_two = next(stage for stage in result.stages if stage.stage == "normalization")
    assert stage_two.metrics["exposure_model_persisted"] is True
    assert stage_one.metrics["documents_loaded"] >= 1
