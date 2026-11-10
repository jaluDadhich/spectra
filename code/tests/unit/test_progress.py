"""Run progress tracker (dashboard progress bar feed)."""
from __future__ import annotations

from app.progress import ProgressTracker, TOTAL_STAGES


def test_progress_advances_with_stages():
    tracker = ProgressTracker()
    tracker.start("RUN-1", "ACCOUNT")
    entry = tracker.get("RUN-1")
    assert entry.state == "QUEUED" and entry.percent == 0.0

    tracker.update("RUN-1", 2, "normalization", "COMPLETED")
    entry = tracker.get("RUN-1")
    assert entry.state == "RUNNING"
    assert entry.percent == round(3 / TOTAL_STAGES * 100, 1)
    assert entry.stage_name == "normalization"

    tracker.update("RUN-1", 11, "findings_synthesis", "COMPLETED")
    tracker.complete("RUN-1", "REVIEW")
    entry = tracker.get("RUN-1")
    assert entry.state == "COMPLETED" and entry.percent == 100.0
    assert "REVIEW" in entry.message


def test_terminal_states_ignore_late_updates():
    tracker = ProgressTracker()
    tracker.start("RUN-1", "A")
    tracker.complete("RUN-1", "PASS")
    tracker.update("RUN-1", 5, "external_truth_checks", "COMPLETED")
    assert tracker.get("RUN-1").percent == 100.0

    tracker.start("RUN-2", "B")
    tracker.fail("RUN-2", "boom")
    tracker.update("RUN-2", 1, "document_inventory", "COMPLETED")
    entry = tracker.get("RUN-2")
    assert entry.state == "FAILED" and entry.message == "boom"


def test_unknown_run_returns_none():
    assert ProgressTracker().get("RUN-NOPE") is None
