"""Deterministic underwriting-quality pillars and weighted overall score.

Each issue family is assigned once to its primary business dimension. Severity
and conclusion state determine a fixed deduction, so identical review results
always produce identical pillar and overall scores. Citation weaknesses and
review completeness are assessed separately under evidence defensibility.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.schemas.review import CheckStatus, ReviewResult
from app.schemas.scoring import QualityDimension, QualityScore, ScoreComponent

_DIMENSIONS = (
    ("evidence_consistency", "Evidence Consistency", 25,
     "Contradictions and conflicts across submitted sources."),
    ("pricing_input_reliability", "Pricing Input Reliability", 20,
     "Freshness and applicability of pricing-sensitive inputs."),
    ("coverage_alignment", "Coverage Alignment", 20,
     "Fit between declared exposures, quoted terms, limits, and forms."),
    ("evidence_defensibility", "Evidence Defensibility", 20,
     "Traceability, support, and completeness of the review evidence."),
    ("exposure_completeness", "Exposure Completeness", 15,
     "Whether material operations and exposures are disclosed and supported."),
)
_SEVERITY_POINTS = {
    "CRITICAL": 24.0, "HIGH": 18.0, "MEDIUM": 12.0,
    "LOW": 5.0, "INFORMATIONAL": 0.0,
}
_CONCLUSION_MULTIPLIER = {
    "CONFIRMED_DATA_CONFLICT": 1.0,
    "POSSIBLE_MISMATCH": 0.7,
    "EVIDENCE_GAP": 0.65,
    "CHECK_INCOMPLETE": 0.6,
    "NO_DISCREPANCY_FOUND": 0.0,
}
_PILLAR_CAP = 80.0
_STALE = re.compile(r"STALE|EXPIRED|OUTDATED|FRESHNESS|RATING.?CURRENCY|OLD.?INPUT", re.I)
_EXPOSURE = re.compile(r"UNDECLARED|UNDERDECLARED|OMITTED.?EXPOSURE|EXPOSURE.?OMISSION|MISSING.?EXPOSURE", re.I)
_COVERAGE = re.compile(r"COVERAGE|LIMIT|SUBLIMIT|EXCLUSION|ENDORSEMENT|CARGO", re.I)
_CONFLICT = re.compile(r"MISMATCH|CONFLICT|RECONCIL|INCONSIST|DUPLICAT|VERSION", re.I)


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").upper()


def _finding_text(finding: Any) -> str:
    return " ".join(_value(getattr(finding, name, "")) for name in (
        "taxonomy_id", "rule_id", "finding_type", "title", "issue_group_title",
        "issue_nature", "possible_consequence", "recommended_action"))


def _primary_dimension(finding: Any) -> str:
    nature = _value(getattr(finding, "issue_nature", ""))
    text = _finding_text(finding)
    # Explicit issue meaning wins; keywords cover deterministic legacy findings
    # and future account-agnostic findings with compatible wording.
    if nature == "EXPOSURE_OMISSION" or _EXPOSURE.search(text):
        return "exposure_completeness"
    if nature == "COVERAGE_OR_LIMITATION" or _COVERAGE.search(text):
        return "coverage_alignment"
    if _STALE.search(text):
        return "pricing_input_reliability"
    if nature in {"EVIDENCE_GAP", "UNSUPPORTED_DECLARATION"}:
        return "evidence_defensibility"
    if nature in {"DATA_MISMATCH", "EXTERNAL_CONFLICT", "DATA_QUALITY", "CHANGE_OVER_TIME"}:
        return "evidence_consistency"
    if _CONFLICT.search(text):
        return "evidence_consistency"
    return "evidence_defensibility"


def _family_key(finding: Any) -> str:
    return (getattr(finding, "issue_group_key", None)
            or getattr(finding, "deduplication_key", None)
            or getattr(finding, "finding_type", None)
            or getattr(finding, "finding_id", "unclassified"))


def _dimension_groups(findings: List[Any]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, Dict[str, Dict[str, Any]]] = {key: {} for key, *_ in _DIMENSIONS}
    for finding in findings:
        dimension = _primary_dimension(finding)
        key = f"{getattr(finding, 'taxonomy_id', 'UNMAPPED')}::{_family_key(finding)}"
        group = groups[dimension].setdefault(key, {
            "severity": "INFORMATIONAL", "conclusion": "NO_DISCREPANCY_FOUND",
            "finding_count": 0, "title": getattr(finding, "issue_group_title", None)
                     or getattr(finding, "title", None) or "Review issue",
        })
        group["finding_count"] += 1
        severity = _value(getattr(finding, "severity", "INFORMATIONAL"))
        if _SEVERITY_POINTS.get(severity, 0) > _SEVERITY_POINTS.get(group["severity"], 0):
            group["severity"] = severity
        conclusion = _value(getattr(finding, "conclusion_state", ""))
        if _CONCLUSION_MULTIPLIER.get(conclusion, 0.5) > _CONCLUSION_MULTIPLIER.get(group["conclusion"], 0.0):
            group["conclusion"] = conclusion
    return {key: list(value.values()) for key, value in groups.items()}


def _defensibility_penalty(result: ReviewResult) -> tuple[float, List[str]]:
    """Apply objective completeness/citation deductions independent of LLM scores."""
    points = 0.0
    notes: List[str] = []
    normalization = next((s for s in result.stages if s.stage == "normalization"), None)
    inventory = next((s for s in result.stages if s.stage == "document_inventory"), None)
    if inventory is not None:
        unsupported = int(inventory.metrics.get("unsupported", 0) or 0)
        points += min(5.0, 3.0 * unsupported)
        if unsupported:
            notes.append(f"{unsupported} unsupported document(s)")
        if inventory.errors:
            points += min(8.0, 4.0 * len(inventory.errors))
            notes.append(f"{len(inventory.errors)} document intake error(s)")
    if normalization is not None:
        missing = normalization.metrics.get("documents_without_evidence") or []
        points += min(10.0, 3.0 * len(missing))
        if missing:
            notes.append(f"{len(missing)} document(s) without evidence")
        if normalization.metrics.get("exposure_model_persisted") is False:
            points += 4.0
            notes.append("exposure model could not be persisted")
    failed_stages = [s.stage for s in result.stages
                     if s.status.value in {"FAILED", "INCOMPLETE"}]
    # Semantic review can be incomplete when explicitly running deterministic-only;
    # such a requested mode is not treated as a source-evidence defect.
    failed_stages = [name for name in failed_stages if name != "gpt_semantic_review"]
    if failed_stages:
        points += min(12.0, 4.0 * len(failed_stages))
        notes.append(f"{len(failed_stages)} pipeline stage(s) incomplete")
    bad_checks = [c for c in result.checks
                  if c.status in {CheckStatus.FAILED, CheckStatus.INCOMPLETE}]
    if bad_checks:
        points += min(12.0, 3.0 * len(bad_checks))
        notes.append(f"{len(bad_checks)} validation check(s) failed or incomplete")
    if result.partial:
        points += 8.0
        notes.append("review used partial inputs")

    # A finding that cannot be traced to any source, or whose citations all lack
    # document locations, weakens defensibility even when its issue type differs.
    citation_groups: Dict[str, List[Any]] = {}
    for finding in result.findings:
        citation_groups.setdefault(str(_family_key(finding)), []).append(finding)
    weak_families = 0
    for family_findings in citation_groups.values():
        refs = [ref for f in family_findings for ref in (getattr(f, "evidence", []) or [])]
        if not refs or all(not getattr(getattr(ref, "source_location", None), "available", False)
                           for ref in refs):
            weak_families += 1
    if weak_families:
        points += min(20.0, 4.0 * weak_families)
        notes.append(f"{weak_families} issue family/families lack traceable source locations")
    return min(points, _PILLAR_CAP), notes


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def quality_issue_groups(findings: List[Any]) -> List[Dict[str, Any]]:
    """Compatibility grouping for the legacy optional severity-task payload."""
    groups: Dict[str, Dict[str, Any]] = {}
    for finding in findings:
        family = _family_key(finding)
        key = f"{getattr(finding, 'taxonomy_id', 'UNMAPPED')}::{family}"
        group = groups.setdefault(key, {
            "group_key": key,
            "title": getattr(finding, "issue_group_title", None)
                     or getattr(finding, "title", None) or "Review issue",
            "finding_ids": [], "finding_count": 0, "severities": [],
            "issue_natures": [], "conclusions": [], "materiality_bands": [],
            "uncertainties": [], "titles": [], "rationales": [],
        })
        group["finding_ids"].append(getattr(finding, "finding_id", ""))
        group["finding_count"] += 1
        group["severities"].append(_value(getattr(finding, "severity", "INFORMATIONAL")))
        group["conclusions"].append(_value(getattr(finding, "conclusion_state", "")))
        group["materiality_bands"].append(_value(getattr(getattr(finding, "materiality", None), "band", "UNKNOWN")))
        nature = _value(getattr(finding, "issue_nature", ""))
        if nature:
            group["issue_natures"].append(nature)
        title = getattr(finding, "title", None)
        if title and title not in group["titles"]:
            group["titles"].append(title)
        rationale = getattr(finding, "severity_rationale", None)
        if rationale and rationale not in group["rationales"]:
            group["rationales"].append(rationale)
        uncertainty = getattr(finding, "uncertainty_reason", None)
        if uncertainty:
            group["uncertainties"].append(uncertainty)
    return list(groups.values())


def score_submission(result: ReviewResult,
                     issue_severity_points: Optional[Dict[str, Dict[str, Any]]] = None
                     ) -> QualityScore:
    """Calculate five deterministic pillar scores and their weighted mean."""
    grouped = _dimension_groups(result.findings)
    defensibility_points, defensibility_notes = _defensibility_penalty(result)
    dimensions: List[QualityDimension] = []
    components: List[ScoreComponent] = []
    for key, title, weight, description in _DIMENSIONS:
        families = grouped[key]
        issue_points = sum(
            _SEVERITY_POINTS.get(group["severity"], 5.0)
            * _CONCLUSION_MULTIPLIER.get(group["conclusion"], 0.5)
            for group in families)
        if key == "evidence_defensibility":
            issue_points += defensibility_points
        points_lost = min(_PILLAR_CAP, issue_points)
        score = round(max(0.0, 100.0 - points_lost), 1)
        details = [f"{len(families)} distinct issue family/families"] if families else []
        if key == "evidence_defensibility":
            details.extend(defensibility_notes)
        detail = "; ".join(details) if details else description
        dimensions.append(QualityDimension(
            key=key, title=title, score=score, weight_pct=float(weight),
            points_lost=round(points_lost, 1), issue_count=len(families),
            finding_count=sum(group["finding_count"] for group in families),
            detail=detail))
        if points_lost:
            components.append(ScoreComponent(
                name=key, points_lost=round(points_lost, 1), detail=detail,
                source="deterministic"))

    overall = round(sum(d.score * d.weight_pct for d in dimensions) / 100.0, 1)
    formula = "Overall = 25% Evidence Consistency + 20% Pricing Input Reliability + " \
              "20% Coverage Alignment + 20% Evidence Defensibility + " \
              "15% Exposure Completeness"
    deduction_policy = (
        "Per distinct issue family: Critical 24, High 18, Medium 12, Low 5 points; "
        "scaled by conclusion certainty, counted once per family, and capped at "
        "80 points per pillar. Evidence Defensibility also reflects citation and "
        "review-completeness gaps."
    )
    return QualityScore(
        score=overall, grade=_grade(overall), dimensions=dimensions,
        components=components, formula=formula, deduction_policy=deduction_policy,
        computed_at=datetime.now(timezone.utc).isoformat(),
        method="spectra-underwriting-quality@3.0.0 · deterministic weighted pillars")
