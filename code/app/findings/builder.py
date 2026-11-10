"""Finding builder: validates drafts against the evidence store, builds
stable finding IDs, and stamps rule/version metadata."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.canonical import CanonicalCase
from app.schemas.findings import (
    Comparison,
    ConclusionState,
    Confidence,
    ConfidenceBasis,
    ComparisonSource,
    EvidenceRef,
    Finding,
    FindingClassification,
    FindingStatus,
    IssueNature,
    Materiality,
    MaterialityBand,
    Severity,
)


class FindingDraft(BaseModel):
    finding_type: str
    taxonomy_id: str
    rule_id: str
    rule_version: str
    title: str
    classification: FindingClassification
    conclusion_state: ConclusionState
    severity: Severity = Severity.MEDIUM
    severity_rationale: Optional[str] = None
    issue_nature: Optional[IssueNature] = None
    materiality_band: MaterialityBand = MaterialityBand.UNKNOWN
    materiality_rationale: str = ""
    confidence_basis: ConfidenceBasis = ConfidenceBasis.RULE_DETERMINISTIC
    confidence_explanation: str = ""
    uncertainty_reason: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    comparison: Optional[Comparison] = None
    issue_group_key: Optional[str] = None
    issue_group_title: Optional[str] = None
    possible_consequence: str = ""
    recommended_action: str = ""
    deduplication_key: str = ""
    blocking: bool = False


class FindingBuilder:
    def __init__(self, case: CanonicalCase):
        self.case = case

    @staticmethod
    def stable_id(submission_id: str, rule_id: str, dedup_key: str) -> str:
        digest = hashlib.sha1(f"{submission_id}|{rule_id}|{dedup_key}".encode()).hexdigest()
        return f"F-{digest[:12].upper()}"

    @staticmethod
    def _infer_issue_nature(finding_type: str) -> IssueNature:
        kind = finding_type.upper()
        if "EVIDENCE_GAP" in kind or "MISSING_REQUIRED" in kind or "DOCUMENT_MISSING" in kind:
            return IssueNature.EVIDENCE_GAP
        if "EXTERNAL" in kind:
            return (IssueNature.EXTERNAL_SOURCE_OF_TRUTH if "VERIFIED" in kind
                    else IssueNature.EXTERNAL_CONFLICT)
        if "OMISSION" in kind or "UNDECLARED" in kind:
            return IssueNature.EXPOSURE_OMISSION
        if "UNSUPPORTED" in kind:
            return IssueNature.UNSUPPORTED_DECLARATION
        if "QUALITY" in kind or "MALFORMED" in kind:
            return IssueNature.DATA_QUALITY
        if "VERSION" in kind or "HISTORICAL" in kind or "TREND" in kind:
            return IssueNature.CHANGE_OVER_TIME
        if "COVERAGE" in kind or "LIMIT" in kind or "EXCLUSION" in kind or "QUESTION" in kind:
            return IssueNature.COVERAGE_OR_LIMITATION
        if "DUPLICATE" in kind or "OVERLAP" in kind:
            return IssueNature.DUPLICATE_OR_OVERLAP
        if any(token in kind for token in ("MISMATCH", "CONFLICT", "RECONCILIATION")):
            return IssueNature.DATA_MISMATCH
        return IssueNature.OTHER

    def build(self, draft: FindingDraft) -> Optional[Finding]:
        """Validate evidence IDs and build the finding. Returns None (and
        records the rejection) if any cited evidence does not exist."""
        now = datetime.now(timezone.utc).isoformat()
        evidence_refs: List[EvidenceRef] = []
        for eid in draft.evidence_ids:
            item = self.case.evidence_store.get(eid)
            if item is None:
                # Hallucinated/invalid citation — reject the draft.
                return None
            evidence_refs.append(EvidenceRef(
                evidence_id=item.evidence_id,
                document_id=item.document_id,
                document_name=item.document_name,
                source_file=item.source_file,
                source_location=item.source_location,
                raw_value=item.raw_value,
                normalized_value=item.normalized_value,
                unit=item.unit,
                period=item.period,
                snippet=item.snippet,
            ))
        if draft.comparison and not draft.comparison.sources:
            grouped = {}
            for item in evidence_refs:
                grouped.setdefault(item.document_id, []).append(item)
            values = draft.comparison.values_compared
            # Only label a value per document when the rule supplied exactly
            # one aggregate value for each cited document. This avoids
            # misrepresenting per-row evidence as aggregate comparison data.
            if values and len(values) == len(grouped):
                draft.comparison.sources = [
                    ComparisonSource(
                        document_id=doc_id,
                        document_name=items[0].document_name,
                        label=items[0].document_name or doc_id,
                        value=values[index],
                        status="COMPARED",
                        evidence_ids=[item.evidence_id for item in items],
                    )
                    for index, (doc_id, items) in enumerate(grouped.items())
                ]
        dedup_key = draft.deduplication_key or f"{draft.rule_id}:{draft.finding_type}"
        group_key = (draft.issue_group_key or
                     f"{draft.taxonomy_id.lower()}_{draft.finding_type.lower()}")
        return Finding(
            finding_id=self.stable_id(self.case.submission_id, draft.rule_id, dedup_key),
            submission_id=self.case.submission_id,
            finding_type=draft.finding_type,
            taxonomy_id=draft.taxonomy_id,
            rule_id=draft.rule_id,
            rule_version=draft.rule_version,
            title=draft.title,
            classification=draft.classification,
            status=FindingStatus.OPEN,
            conclusion_state=draft.conclusion_state,
            severity=draft.severity,
            severity_rationale=(draft.severity_rationale or
                "Configured deterministic rule severity; LLM judge unavailable for this fallback run."),
            issue_nature=draft.issue_nature or self._infer_issue_nature(draft.finding_type),
            issue_group_key=group_key,
            issue_group_title=draft.issue_group_title or draft.title,
            materiality=Materiality(band=draft.materiality_band,
                                    rationale=draft.materiality_rationale,
                                    impact_estimate=None),
            confidence=Confidence(value=None, basis=draft.confidence_basis,
                                  calibrated=False,
                                  explanation=draft.confidence_explanation),
            uncertainty_reason=draft.uncertainty_reason,
            evidence=evidence_refs,
            comparison=draft.comparison,
            possible_consequence=draft.possible_consequence,
            recommended_action=draft.recommended_action,
            estimated_impact=None,
            deduplication_key=dedup_key,
            blocking=draft.blocking,
            created_at=now,
            updated_at=now,
        )
