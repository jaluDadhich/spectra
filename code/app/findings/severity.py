"""Severity/materiality ranking and case-status precedence (spec Stage 10/11).

Status precedence (safe default, configurable):
ERROR/INCOMPLETE > HOLD > REVIEW > PASS
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.schemas.findings import (
    CaseStatus,
    ConclusionState,
    Finding,
    FindingStatus,
    MaterialityBand,
    Severity,
)
from app.schemas.review import CheckOutcome, CheckStatus

_SEV_SCORE = {
    Severity.CRITICAL: 50, Severity.HIGH: 35, Severity.MEDIUM: 20,
    Severity.LOW: 10, Severity.INFORMATIONAL: 2,
}
_MAT_SCORE = {
    MaterialityBand.HIGH: 30, MaterialityBand.MEDIUM: 20,
    MaterialityBand.LOW: 10, MaterialityBand.UNKNOWN: 5,
}
_CONCLUSION_SCORE = {
    ConclusionState.CONFIRMED_DATA_CONFLICT: 20,
    ConclusionState.POSSIBLE_MISMATCH: 12,
    ConclusionState.EVIDENCE_GAP: 8,
    ConclusionState.CHECK_INCOMPLETE: 8,
    ConclusionState.NO_DISCREPANCY_FOUND: 0,
}
_TERMINAL = {
    FindingStatus.RESOLVED, FindingStatus.WAIVED,
    FindingStatus.NOT_APPLICABLE, FindingStatus.REJECTED_FALSE_POSITIVE,
}


def rank_findings(findings: List[Finding]) -> List[Finding]:
    """Deterministic, explainable ranking. Score components are stored on
    the finding's materiality rationale-adjacent metadata via comparison
    object untouched; the score itself is returned sorted only."""

    def score(f: Finding) -> int:
        if f.status in _TERMINAL:
            return -1
        return (
            _SEV_SCORE[f.severity]
            + _MAT_SCORE[f.materiality.band]
            + _CONCLUSION_SCORE[f.conclusion_state]
            + (15 if f.blocking else 0)
            + min(len(f.evidence), 5)
        )

    return sorted(findings, key=lambda f: (-score(f), f.finding_id))


def compute_case_status(
    findings: List[Finding],
    checks: List[CheckOutcome],
    run_errors: Optional[List[str]] = None,
    blocking_policy: Optional[Dict[str, bool]] = None,
) -> CaseStatus:
    """Safe-default precedence:
    1. ERROR/INCOMPLETE — any required check failed/incomplete or run error.
    2. HOLD — a blocking finding is open.
    3. REVIEW — any open material/ambiguous finding remains.
    4. PASS — everything else completed cleanly.
    """
    if run_errors:
        return CaseStatus.ERROR_INCOMPLETE
    for check in checks:
        if check.status in {CheckStatus.FAILED, CheckStatus.INCOMPLETE}:
            return CaseStatus.ERROR_INCOMPLETE
    open_findings = [f for f in findings if f.status not in _TERMINAL]
    if any(f.blocking for f in open_findings):
        return CaseStatus.HOLD
    if open_findings:
        return CaseStatus.REVIEW
    return CaseStatus.PASS
