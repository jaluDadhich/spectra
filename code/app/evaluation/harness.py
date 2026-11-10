"""Evaluation harness (spec section 14).

Ground-truth labels live ONLY under evaluation/ground_truth/ and are
loaded exclusively by evaluation commands/tests — never by the runtime
review path.

Matching is by unified category ID, not titles. The runtime's complete
issue taxonomy is U01-U14; each finding must map to exactly one category.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.schemas.review import ReviewResult


class ExpectedIssue(BaseModel):
    taxonomy_id: str
    manifested: bool = False
    note: str = ""


class GroundTruth(BaseModel):
    account: str
    expected: List[ExpectedIssue] = Field(default_factory=list)
    excluded: List[str] = Field(default_factory=list)  # labels that must NOT be reported


def load_ground_truth(path: Path) -> GroundTruth:
    return GroundTruth.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def _matches(expected_label: str, finding_taxonomy: str) -> bool:
    return expected_label == finding_taxonomy


def evaluate_run(result: ReviewResult, ground_truth_path: Path) -> Dict[str, Any]:
    gt = load_ground_truth(ground_truth_path)
    findings = result.findings

    detected_expected: Dict[str, List[str]] = {}
    for exp in gt.expected:
        hits = [f.finding_id for f in findings if _matches(exp.taxonomy_id, f.taxonomy_id)]
        if hits:
            detected_expected[exp.taxonomy_id] = hits
    false_negatives = [e.taxonomy_id for e in gt.expected if e.taxonomy_id not in detected_expected]

    expected_labels = [e.taxonomy_id for e in gt.expected]
    false_positives = [
        f.finding_id for f in findings
        if not any(_matches(e, f.taxonomy_id) for e in expected_labels)
        and f.taxonomy_id not in {"DOC", "DQ", "GPT"}  # cross-cutting families are not FPs
    ]
    excluded_violations = [
        f.finding_id for f in findings
        if any(_matches(x, f.taxonomy_id) or f.taxonomy_id == x for x in gt.excluded)
    ]

    tp = len(detected_expected)
    fp = len(false_positives)
    fn = len(false_negatives)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    manifested = [e for e in gt.expected if e.manifested]
    manifested_hits = sum(1 for e in manifested if e.taxonomy_id in detected_expected)

    # duplicate-alert rate: findings sharing dedup keys post-dedup (should be 0)
    keys = [f.deduplication_key for f in findings]
    duplicate_rate = (len(keys) - len(set(keys))) / len(keys) if keys else 0.0
    # evidence citation accuracy: findings whose every citation is resolvable
    cited_ok = sum(
        1 for f in findings
        if f.evidence and all(e.evidence_id for e in f.evidence))
    citation_accuracy = cited_ok / len(findings) if findings else 1.0

    per_family = {}
    for e in gt.expected:
        per_family[e.taxonomy_id] = {
            "detected": e.taxonomy_id in detected_expected,
            "manifested": e.manifested,
            "finding_ids": detected_expected.get(e.taxonomy_id, []),
            "note": e.note,
        }

    return {
        "account": gt.account,
        "run_id": result.run_id,
        "case_status": result.status.value,
        "summary": {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "manifested_recall": (
                round(manifested_hits / len(manifested), 3) if manifested else None),
            "excluded_violations": excluded_violations,
            "duplicate_alert_rate": round(duplicate_rate, 3),
            "evidence_citation_accuracy": round(citation_accuracy, 3),
        },
        "per_family": per_family,
        "false_positive_findings": false_positives,
        "undetected": false_negatives,
        "token_usage": result.token_usage.model_dump(),
    }
