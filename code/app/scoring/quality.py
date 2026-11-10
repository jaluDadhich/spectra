"""Explainable submission-quality score (0-100, grade A-F).

Computed after every completed review from the persisted ReviewResult, so
the same inputs always produce the same score and the score of historical
runs can be recomputed.

Penalty model (documented and explainable):
- Document intake: stage errors, unrecognized documents, documents that
  yielded no evidence.
- Extraction reliability: failed LLM exposure extractions, unpersisted
  exposure model.
- Check reliability: FAILED or INCOMPLETE checks.
- Issue severity: one LLM-scored deduction per distinct issue family, with a
  deterministic deduplicated fallback when the fast model is unavailable.

Each component is capped so no single failure mode dominates, and every
deduction is itemized in the score payload.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.schemas.review import CheckStatus, ReviewResult
from app.schemas.scoring import QualityScore, ScoreComponent

_SEVERITY_PENALTY = {"CRITICAL": 10.0, "HIGH": 6.0, "MEDIUM": 3.0,
                     "LOW": 1.0, "INFORMATIONAL": 0.0}
_CAPS = {"intake": 24.0, "evidence": 16.0, "extraction": 12.0,
         "checks": 15.0, "issues": 40.0}
_INTAKE_STAGES = {"case_initialization", "document_inventory", "normalization"}
_ACCEPTABLE_EXTRACTION = {"ACCEPTED", "NO_EXPOSURES", "NO_SOURCE_DATA"}
_SEVERITY_RANK = {"INFORMATIONAL": 0, "LOW": 1, "MEDIUM": 2,
                  "HIGH": 3, "CRITICAL": 4}


def quality_issue_groups(findings) -> List[Dict[str, Any]]:
    """Collapse repeated findings into distinct issue families for scoring."""
    groups: Dict[str, Dict[str, Any]] = {}
    for finding in findings:
        family = (finding.issue_group_key or finding.finding_type
                  or finding.rule_id or finding.title or "unclassified_issue")
        key = f"{finding.taxonomy_id or 'UNMAPPED'}::{family}"
        group = groups.setdefault(key, {
            "group_key": key,
            "title": finding.issue_group_title or finding.title or finding.finding_type,
            "finding_ids": [], "finding_count": 0, "severities": [],
            "issue_natures": [], "conclusions": [], "materiality_bands": [],
            "uncertainties": [], "titles": [], "rationales": [],
        })
        group["finding_ids"].append(finding.finding_id)
        group["finding_count"] += 1
        group["severities"].append(finding.severity.value)
        group["conclusions"].append(finding.conclusion_state.value)
        group["materiality_bands"].append(finding.materiality.band.value)
        if finding.uncertainty_reason:
            group["uncertainties"].append(finding.uncertainty_reason)
        if finding.issue_nature:
            group["issue_natures"].append(finding.issue_nature.value)
        if finding.title and finding.title not in group["titles"]:
            group["titles"].append(finding.title)
        if finding.severity_rationale and finding.severity_rationale not in group["rationales"]:
            group["rationales"].append(finding.severity_rationale)
    return list(groups.values())


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


def score_submission(result: ReviewResult,
                     issue_severity_points: Optional[Dict[str, Dict[str, Any]]] = None
                     ) -> QualityScore:
    """Score the submitted data quality for a completed review run."""
    components: List[ScoreComponent] = []
    stages = {stage.stage: stage for stage in result.stages}

    # -- Intake / document completeness -----------------------------------
    intake_lost = 0.0
    details: List[str] = []
    inventory = stages.get("document_inventory")
    if inventory is not None:
        unsupported = int(inventory.metrics.get("unsupported", 0) or 0)
        if unsupported:
            intake_lost += 3.0 * unsupported
            details.append(f"{unsupported} unsupported document(s)")
        if inventory.errors:
            intake_lost += 4.0 * len(inventory.errors)
            details.append(f"{len(inventory.errors)} intake error(s)")
    for stage_name in _INTAKE_STAGES:
        stage = stages.get(stage_name)
        if stage is not None and stage.status.value in {"FAILED", "INCOMPLETE"}:
            intake_lost += 4.0
            details.append(f"stage {stage_name} ended {stage.status.value}")
    if intake_lost:
        components.append(ScoreComponent(
            name="document_completeness", points_lost=min(intake_lost, _CAPS["intake"]),
            detail="; ".join(details)))

    # -- Evidence coverage --------------------------------------------------
    evidence_lost = 0.0
    normalization = stages.get("normalization")
    details = []
    if normalization is not None:
        no_evidence = normalization.metrics.get("documents_without_evidence") or []
        if no_evidence:
            evidence_lost += 2.0 * len(no_evidence)
            details.append(f"{len(no_evidence)} document(s) produced no evidence")
        if normalization.metrics.get("exposure_model_persisted") is False:
            evidence_lost += 4.0
            details.append("exposure model snapshot could not be persisted")
    if evidence_lost:
        components.append(ScoreComponent(
            name="evidence_coverage", points_lost=min(evidence_lost, _CAPS["evidence"]),
            detail="; ".join(details)))

    # -- Extraction reliability ---------------------------------------------
    extraction_lost = 0.0
    details = []
    if normalization is not None:
        statuses = normalization.metrics.get("llm_extraction_status") or {}
        failed = [doc for doc, status in statuses.items()
                  if status not in _ACCEPTABLE_EXTRACTION and status != "NOT_ATTEMPTED"]
        if failed:
            extraction_lost += 3.0 * len(failed)
            details.append(
                f"extraction incomplete/rejected for {len(failed)} document(s)")
    if extraction_lost:
        components.append(ScoreComponent(
            name="extraction_reliability",
            points_lost=min(extraction_lost, _CAPS["extraction"]),
            detail="; ".join(details)))

    # -- Check reliability ---------------------------------------------------
    bad_checks = [c for c in result.checks
                  if c.status in {CheckStatus.FAILED, CheckStatus.INCOMPLETE}]
    if bad_checks:
        components.append(ScoreComponent(
            name="check_reliability",
            points_lost=min(3.0 * len(bad_checks), _CAPS["checks"]),
            detail="; ".join(f"{c.check_id} {c.status.value}" for c in bad_checks[:4])))

    # -- Issue severity -------------------------------------------------------
    issue_groups = quality_issue_groups(result.findings)
    expected_group_keys = {group["group_key"] for group in issue_groups}
    llm_scores_valid = bool(issue_groups) and isinstance(issue_severity_points, dict) \
        and set(issue_severity_points) == expected_group_keys \
        and all(isinstance(value, dict)
                and isinstance(value.get("points_lost"), (int, float))
                and 0 <= value["points_lost"] <= 10
                and str(value.get("rationale") or "").strip()
                for value in issue_severity_points.values())
    issue_lost = 0.0
    details = []
    for group in issue_groups:
        if llm_scores_valid:
            decision = issue_severity_points[group["group_key"]]
            points = float(decision["points_lost"])
            rationale = str(decision["rationale"]).strip()
        else:
            highest = max(group["severities"], key=lambda s: _SEVERITY_RANK.get(s, 0))
            points = _SEVERITY_PENALTY.get(highest, 0.5)
            rationale = f"{highest.title()} severity; counted once for this issue family"
        issue_lost += points
        details.append(
            f"{group['title']} ({group['finding_count']} finding(s)): {rationale}")
    if issue_lost:
        capped = min(issue_lost, _CAPS["issues"])
        components.append(ScoreComponent(
            name="issue_severity", points_lost=capped,
            detail=(f"{len(issue_groups)} unique issue group(s); "
                    + "; ".join(details[:4])
                    + (f"; +{len(details) - 4} more group(s)" if len(details) > 4 else "")),
            source="LLM" if llm_scores_valid else "deterministic fallback"))

    lost = sum(c.points_lost for c in components)
    score = round(max(0.0, 100.0 - lost), 1)
    return QualityScore(
        score=score, grade=_grade(score), components=components,
        computed_at=datetime.now(timezone.utc).isoformat(),
        method=("spectra-quality-score@2.0.0 · LLM issue points"
                if llm_scores_valid else
                "spectra-quality-score@2.0.0 · deterministic issue fallback"))
