"""Versioned persistence for canonical exposure models."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.schemas.canonical import CanonicalCase

MODEL_SCHEMA_VERSION = "1.0"


def _safe_key(value: str) -> str:
    # Hash identifiers instead of using user-controlled submission/document IDs
    # as path components.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_exposure_model(case: CanonicalCase,
                         extraction_status: Optional[Dict[str, str]] = None
                         ) -> Dict[str, Any]:
    docs = {doc.document_id: doc for doc in case.documents}
    by_document: Dict[str, List[Dict[str, Any]]] = {}
    for exposure in case.exposures:
        evidence = {
            name: case.evidence_store[evidence_id].model_dump(mode="json")
            for name, evidence_id in exposure.field_evidence.items()
            if evidence_id in case.evidence_store
        }
        by_document.setdefault(exposure.document_id, []).append({
            "record": exposure.model_dump(mode="json"),
            "evidence": evidence,
        })
    return {
        "schema_version": MODEL_SCHEMA_VERSION,
        "submission_id": case.submission_id,
        "run_id": case.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "documents": [
            {
                "document_id": document_id,
                "document_type": docs[document_id].document_type
                if document_id in docs else "unknown",
                "document_name": docs[document_id].document_name
                if document_id in docs else None,
                "source_hash": docs[document_id].source_hash
                if document_id in docs else None,
                "content_hash": docs[document_id].content_hash
                if document_id in docs else None,
                "extraction_status": (extraction_status or {}).get(document_id, "DETERMINISTIC"),
                "exposures": by_document.get(document_id, []),
            }
            for document_id in sorted(set(docs) | set(by_document))
        ],
    }


def save_exposure_model(data_dir: Path, case: CanonicalCase,
                        extraction_status: Optional[Dict[str, str]] = None) -> Path:
    """Atomically persist one immutable model snapshot per review run."""
    target_dir = Path(data_dir) / "extracted_models" / _safe_key(case.submission_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_safe_key(case.run_id)}.json"
    payload = json.dumps(
        build_exposure_model(case, extraction_status),
        ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=".extracted-model-", suffix=".tmp", dir=str(target_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return target
