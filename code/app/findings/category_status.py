"""Per-category (U01-U14) assessment derivation.

Every submission is evaluated against all 14 unified issue categories.
The status of each category is derived deterministically from:

1. Applicability signals (document types / record kinds present in the
   canonical case) — an account without the exposure context is
   NOT_APPLICABLE, never silently skipped.
2. Check outcomes of the deterministic rules mapped to the category.
3. The conclusion states of the findings assigned to the category.

Missing evidence is reported as NOT_ASSESSABLE (or surfaced via an
evidence-gap finding), never as a clean result.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.catalog.issue_register import IssueCategory, IssueRegister
from app.schemas.canonical import CanonicalCase
from app.schemas.findings import (
    CategoryAssessment,
    CategoryStatus,
    ConclusionState,
    Finding,
)
from app.schemas.review import CheckOutcome, CheckStatus

_RECORD_KIND_ATTRS = {
    "entities": "entities",
    "locations": "locations",
    "vehicles": "vehicles",
    "drivers": "drivers",
    "claims": "claims",
    "wc_payroll_rows": "wc_payroll_rows",
    "rating_factors": "rating_factors",
    "quote_terms": "quote_terms",
    "exposures": "exposures",
    "external_observations": "external_observations",
    "checklist_items": "checklist_items",
    "narratives": "narratives",
}


def _is_applicable(category: IssueCategory, case: CanonicalCase) -> bool:
    signals = category.applicability
    if not signals.document_types_any and not signals.record_kinds_any:
        return True
    present_types = {d.document_type for d in case.documents}
    if present_types & set(signals.document_types_any):
        return True
    for kind in signals.record_kinds_any:
        attr = _RECORD_KIND_ATTRS.get(kind)
        if attr and getattr(case, attr, None):
            return True
    return False


def derive_category_assessments(
    case: CanonicalCase,
    register: IssueRegister,
    findings: List[Finding],
    checks: List[CheckOutcome],
    semantic_review_completed: bool = False,
) -> List[CategoryAssessment]:
    """Return one assessment per register category, ordered U01..U14."""
    checks_by_id: Dict[str, CheckOutcome] = {c.check_id: c for c in checks}
    findings_by_category: Dict[str, List[Finding]] = {}
    for finding in findings:
        findings_by_category.setdefault(finding.taxonomy_id, []).append(finding)

    assessments: List[CategoryAssessment] = []
    for category in register.issues:
        cat_findings = findings_by_category.get(category.id, [])
        finding_ids = [f.finding_id for f in cat_findings]

        if not _is_applicable(category, case):
            assessments.append(CategoryAssessment(
                issue_id=category.id, category=category.title,
                primary_area=category.primary_area,
                status=CategoryStatus.NOT_APPLICABLE,
                rationale="The submission has no exposure or document context for this category",
                finding_ids=[]))
            continue

        conclusion_states = {f.conclusion_state for f in cat_findings}
        if ConclusionState.CONFIRMED_DATA_CONFLICT in conclusion_states:
            status = CategoryStatus.CONFIRMED
            rationale = ("Supplied evidence directly supports the reported discrepancy "
                         f"({len(cat_findings)} finding(s))")
        elif ConclusionState.POSSIBLE_MISMATCH in conclusion_states:
            status = CategoryStatus.POTENTIAL
            rationale = ("Indicator present, but a required match, scope definition or "
                         "supporting fact is unresolved")
        elif conclusion_states == {ConclusionState.EVIDENCE_GAP}:
            status = CategoryStatus.NOT_ASSESSABLE
            rationale = ("Category is relevant, but required documents or fields are absent: "
                         + "; ".join(f.title for f in cat_findings[:3]))
        elif not cat_findings:
            rule_checks = [checks_by_id[rid] for rid in category.rule_ids if rid in checks_by_id]
            failed = [c for c in rule_checks if c.status == CheckStatus.FAILED]
            incomplete = [c for c in rule_checks if c.status != CheckStatus.COMPLETED]
            if failed:
                status = CategoryStatus.NOT_ASSESSABLE
                rationale = ("Category checks failed: "
                             + "; ".join(c.detail for c in failed[:2]))
            elif rule_checks and not incomplete:
                status = CategoryStatus.NO_DISCREPANCY
                rationale = ("Category was checked with adequate evidence and the tested "
                             "values reconciled")
            elif not rule_checks and semantic_review_completed:
                status = CategoryStatus.NO_DISCREPANCY
                rationale = "The complete submission review found no supported issue in this category"
            elif rule_checks or not semantic_review_completed:
                status = CategoryStatus.NOT_ASSESSABLE
                rationale = ("Category is relevant, but the required source documents or "
                             "fields were absent or insufficient for a comparison")
            else:
                status = CategoryStatus.NOT_ASSESSABLE
                rationale = "No check could run for this category with the supplied data"
        else:  # only CHECK_INCOMPLETE-style leftovers
            status = CategoryStatus.NOT_ASSESSABLE
            rationale = "Category could not be fully assessed with the supplied evidence"

        assessments.append(CategoryAssessment(
            issue_id=category.id, category=category.title,
            primary_area=category.primary_area, status=status,
            rationale=rationale, finding_ids=finding_ids))
    return assessments
