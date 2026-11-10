"""SQLite case persistence and append-only audit trail (spec section 13).

SQLite is the bundled local-prototype store. The repository interface is
deliberately narrow so a production RDBMS can replace it via migrations;
do not treat SQLite as sufficient for multi-user production.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.schemas.findings import Finding, FindingStatus, ReviewerDisposition
from app.schemas.review import ReviewResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL,
    status TEXT NOT NULL,
    input_hash TEXT,
    config_versions TEXT,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    taxonomy_id TEXT,
    severity TEXT,
    status TEXT,
    finding_json TEXT NOT NULL,
    PRIMARY KEY (run_id, finding_id)
);
CREATE TABLE IF NOT EXISTS dispositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    disposition_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    actor TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS token_usage (
    run_id TEXT PRIMARY KEY,
    usage_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_search_cache (
    cache_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    result_json TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_memory (
    memory_key TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    feedback_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (memory_key, agent_name)
);
"""


class CaseRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # -- runs ------------------------------------------------------------
    def save_run(self, result: ReviewResult, input_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, submission_id, status, input_hash,"
                " config_versions, result_json, created_at, completed_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (result.run_id, result.submission_id, result.status.value, input_hash,
                 json.dumps(result.config_versions), result.model_dump_json(),
                 result.created_at, result.completed_at))
            conn.execute("DELETE FROM findings WHERE run_id = ?", (result.run_id,))
            for f in result.findings:
                conn.execute(
                    "INSERT INTO findings (run_id, finding_id, taxonomy_id, severity,"
                    " status, finding_json) VALUES (?,?,?,?,?,?)",
                    (result.run_id, f.finding_id, f.taxonomy_id, f.severity.value,
                     f.status.value, f.model_dump_json()))
            conn.execute(
                "INSERT OR REPLACE INTO token_usage (run_id, usage_json) VALUES (?,?)",
                (result.run_id, result.token_usage.model_dump_json()))

    def get_run(self, run_id: str) -> Optional[ReviewResult]:
        with self._connect() as conn:
            row = conn.execute("SELECT result_json FROM runs WHERE run_id = ?",
                               (run_id,)).fetchone()
        return ReviewResult.model_validate_json(row["result_json"]) if row else None

    def list_runs(self, submission_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if submission_id:
                rows = conn.execute(
                    "SELECT run_id, submission_id, status, created_at, completed_at"
                    " FROM runs WHERE submission_id = ? ORDER BY created_at DESC",
                    (submission_id,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT run_id, submission_id, status, created_at, completed_at"
                    " FROM runs ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_runs_for_submission(self, submission_id: str,
                                   keep_run_id: Optional[str] = None) -> int:
        """Override semantics for account runs: remove prior runs for the
        account so the dashboard shows exactly one current run per account.
        Audit events are intentionally preserved."""
        with self._connect() as conn:
            params: List[Any] = [submission_id]
            sql = "SELECT run_id FROM runs WHERE submission_id = ?"
            if keep_run_id:
                sql += " AND run_id != ?"
                params.append(keep_run_id)
            run_ids = [r["run_id"] for r in conn.execute(sql, params).fetchall()]
            for run_id in run_ids:
                for table in ("findings", "token_usage", "dispositions", "runs"):
                    conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
        return len(run_ids)

    def find_run_by_input_hash(self, submission_id: str, input_hash: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run_id FROM runs WHERE submission_id = ? AND input_hash = ?"
                " ORDER BY created_at DESC LIMIT 1", (submission_id, input_hash)).fetchone()
        return row["run_id"] if row else None

    # -- dispositions ------------------------------------------------------
    def record_disposition(self, run_id: str, finding_id: str,
                           disposition: ReviewerDisposition) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO dispositions (run_id, finding_id, disposition_json, created_at)"
                " VALUES (?,?,?,?)",
                (run_id, finding_id, disposition.model_dump_json(), now))
            row = conn.execute(
                "SELECT finding_json FROM findings WHERE run_id = ? AND finding_id = ?",
                (run_id, finding_id)).fetchone()
            if row:
                finding = Finding.model_validate_json(row["finding_json"])
                finding.disposition_history.append(disposition)
                finding.reviewer_disposition = disposition
                finding.status = FindingStatus(disposition.new_status)
                finding.updated_at = now
                conn.execute(
                    "UPDATE findings SET status = ?, finding_json = ?"
                    " WHERE run_id = ? AND finding_id = ?",
                    (finding.status.value, finding.model_dump_json(), run_id, finding_id))
            run_row = conn.execute("SELECT result_json FROM runs WHERE run_id = ?",
                                   (run_id,)).fetchone()
            if run_row:
                result = ReviewResult.model_validate_json(run_row["result_json"])
                for f in result.findings:
                    if f.finding_id == finding_id:
                        f.disposition_history.append(disposition)
                        f.reviewer_disposition = disposition
                        f.status = FindingStatus(disposition.new_status)
                        f.updated_at = now
                conn.execute("UPDATE runs SET result_json = ? WHERE run_id = ?",
                             (result.model_dump_json(), run_id))
                memory_key = result.config_versions.get("account_memory_key")
                if memory_key:
                    agent_name = _agent_for_finding(finding)
                    row = conn.execute(
                        "SELECT feedback_json FROM agent_memory WHERE memory_key = ? AND agent_name = ?",
                        (memory_key, agent_name)).fetchone()
                    feedback = json.loads(row["feedback_json"]) if row else []
                    feedback.append({
                        "taxonomy_id": finding.taxonomy_id,
                        "finding_type": finding.finding_type,
                        "action": disposition.action.value,
                        "rationale": disposition.rationale,
                        "reviewer_id": disposition.reviewer_id,
                        "timestamp": disposition.timestamp,
                    })
                    feedback = feedback[-20:]
                    conn.execute(
                        "INSERT OR REPLACE INTO agent_memory "
                        "(memory_key, agent_name, feedback_json, updated_at) VALUES (?,?,?,?)",
                        (memory_key, agent_name, json.dumps(feedback), now))

    def get_finding(self, run_id: str, finding_id: str) -> Optional[Finding]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT finding_json FROM findings WHERE run_id = ? AND finding_id = ?",
                (run_id, finding_id)).fetchone()
        return Finding.model_validate_json(row["finding_json"]) if row else None

    # -- audit ---------------------------------------------------------------
    def audit(self, action: str, run_id: Optional[str] = None,
              actor: str = "system", detail: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events (run_id, actor, action, detail, created_at)"
                " VALUES (?,?,?,?,?)",
                (run_id, actor, action, detail[:2000],
                 datetime.now(timezone.utc).isoformat()))

    def audit_trail(self, run_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT actor, action, detail, created_at FROM audit_events"
                " WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    # -- durable agent memory --------------------------------------------
    def get_agent_memory(self, memory_key: str, agent_name: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT feedback_json FROM agent_memory WHERE memory_key = ? AND agent_name = ?",
                (memory_key, agent_name)).fetchone()
        return json.loads(row["feedback_json"]) if row else []

    # -- external source result cache ------------------------------------
    def get_external_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json, expires_at FROM external_search_cache WHERE cache_key = ?",
                (cache_key,)).fetchone()
            if not row:
                return None
            if row["expires_at"] <= now:
                conn.execute("DELETE FROM external_search_cache WHERE cache_key = ?",
                             (cache_key,))
                return None
        cached = json.loads(row["result_json"])
        if isinstance(cached, dict) and "payload" in cached:
            payload = dict(cached["payload"])
            payload["_cache_retrieved_at"] = cached.get("retrieved_at")
            return payload
        return cached

    def put_external_cache(self, cache_key: str, source: str,
                           result: Dict[str, Any], ttl_hours: int) -> None:
        now_dt = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO external_search_cache "
                "(cache_key, source, result_json, retrieved_at, expires_at) VALUES (?,?,?,?,?)",
                 (cache_key, source,
                  json.dumps({"retrieved_at": now_dt.isoformat(), "payload": result}, default=str),
                  now_dt.isoformat(),
                 (now_dt + timedelta(hours=ttl_hours)).isoformat()))


def _agent_for_finding(finding: Finding) -> str:
    text = (f"{finding.finding_type} {finding.rule_id} {finding.taxonomy_id} "
            f"{finding.deduplication_key}").lower()
    for marker, agent in (("wc", "wc_agent"), ("gl", "gl_agent"),
                          ("auto", "auto_agent"), ("property", "property_agent")):
        if marker in text:
            return agent
    return "submission_agent"
