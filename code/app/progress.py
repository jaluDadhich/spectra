"""In-memory, thread-safe run progress tracker.

Feeds the dashboard's per-run progress bar. Progress is derived from the
12 review stages; entries live only for the lifetime of the process, and
completed runs are reconstructed from the repository when the tracker no
longer has an entry.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, Optional

from pydantic import BaseModel

TOTAL_STAGES = 12  # stages 0..11 in app.agents.orchestrator._STAGE_NAMES


class RunProgress(BaseModel):
    run_id: str
    account: str = ""
    state: str = "QUEUED"  # QUEUED | RUNNING | COMPLETED | FAILED
    percent: float = 0.0
    stage_index: int = -1
    stage_name: str = "queued"
    message: str = "Queued"
    updated_at: str = ""


class ProgressTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[str, RunProgress] = {}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def start(self, run_id: str, account: str) -> RunProgress:
        entry = RunProgress(run_id=run_id, account=account,
                            updated_at=self._now())
        with self._lock:
            self._entries[run_id] = entry
        return entry

    def update(self, run_id: str, stage_index: int, stage_name: str,
               status: str = "COMPLETED") -> None:
        """Advance the bar: stage ``stage_index`` finished with ``status``."""
        with self._lock:
            entry = self._entries.get(run_id)
            if entry is None or entry.state in {"COMPLETED", "FAILED"}:
                return
            entry.state = "RUNNING"
            entry.stage_index = stage_index
            entry.stage_name = stage_name
            entry.percent = round(min(100.0, (stage_index + 1) / TOTAL_STAGES * 100), 1)
            entry.message = f"{stage_name} {status.lower()}"
            entry.updated_at = self._now()

    def complete(self, run_id: str, status: str) -> None:
        with self._lock:
            entry = self._entries.get(run_id)
            if entry is None:
                return
            entry.state = "COMPLETED"
            entry.percent = 100.0
            entry.message = f"Review finished with status {status}"
            entry.updated_at = self._now()

    def fail(self, run_id: str, message: str) -> None:
        with self._lock:
            entry = self._entries.get(run_id)
            if entry is None:
                return
            entry.state = "FAILED"
            entry.message = message[:300]
            entry.updated_at = self._now()

    def get(self, run_id: str) -> Optional[RunProgress]:
        with self._lock:
            entry = self._entries.get(run_id)
            return entry.model_copy() if entry else None
