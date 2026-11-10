"""Evaluation harness tests against isolated ground truth."""
from __future__ import annotations

from pathlib import Path

from app.evaluation import evaluate_run

ROOT = Path(__file__).resolve().parent.parent.parent


def test_evaluate_a01(review_runner):
    result = review_runner("a01", "SPX-A01-2026-0101")
    report = evaluate_run(result, ROOT / "evaluation" / "ground_truth" / "a01.json")
    s = report["summary"]
    assert s["false_negatives"] == 0
    assert s["false_positives"] == 0
    assert s["manifested_recall"] == 1.0
    assert s["excluded_violations"] == []
    assert s["duplicate_alert_rate"] == 0.0


def test_evaluate_a02_n5_not_double_counted(review_runner):
    result = review_runner("a02", "SPX-A02-2026-0202")
    report = evaluate_run(result, ROOT / "evaluation" / "ground_truth" / "a02.json")
    assert report["summary"]["excluded_violations"] == []
    assert report["summary"]["recall"] == 1.0


def test_evaluate_a03_n14_excluded(review_runner):
    result = review_runner("a03", "SPX-A03-2026-1042")
    report = evaluate_run(result, ROOT / "evaluation" / "ground_truth" / "a03.json")
    assert report["summary"]["excluded_violations"] == []
    assert report["summary"]["false_negatives"] == 0


def test_ground_truth_not_accessible_to_runtime(review_runner):
    """Runtime config versions must never reference evaluation labels."""
    result = review_runner("a01", "SPX-A01-2026-0101")
    assert "ground_truth" not in str(result.config_versions).lower()
