"""Input loading, validation, and canonical-case assembly (spec Stage 1/2).

- Validates JSON syntax and envelope shape.
- Never silently omits malformed inputs: they are reported, and the case
  proceeds in PARTIAL mode only when allowed.
- Keeps original JSON immutable; assigns generated IDs marked as such.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.catalog import get_registry
from app.schemas.canonical import CanonicalCase, EvidenceItem, SourceLocation
from app.schemas.envelope import InputEnvelope


class InputError(BaseException):
    pass


_AUXILIARY_JSON_NAMES = {"extraction_report.json", "validation_report.json"}
_REFERENCE_NAME_MARKERS = (
    "issue_manifest", "selected_manifested_and_nonmanifested_issues",
    "ground_truth", "expected_issues", "evaluation_reference",
)
_PLAIN_TEXT_EXTENSIONS = {".txt", ".md", ".eml"}


def is_auxiliary_json(path: Path) -> bool:
    """Reports describe extraction/validation; they are not submission docs."""
    return path.name.casefold() in _AUXILIARY_JSON_NAMES


def is_reference_artifact(path: Path) -> bool:
    """Keep evaluation answers/manifests out of model input to prevent leakage."""
    name = re.sub(r"[^a-z0-9]+", "_", path.stem.casefold()).strip("_")
    return any(marker in name for marker in _REFERENCE_NAME_MARKERS)


def submission_input_paths(folder: Path) -> List[Path]:
    """List extracted JSON inputs and directly readable text submission files."""
    paths = [p for p in folder.glob("*.json")
             if not is_auxiliary_json(p) and not is_reference_artifact(p)]
    paths.extend(p for p in folder.iterdir()
                 if p.is_file() and p.suffix.casefold() in _PLAIN_TEXT_EXTENSIONS
                 and not is_reference_artifact(p))
    return sorted(paths, key=lambda path: path.name.casefold())


class LoadedDocument:
    def __init__(self, envelope: InputEnvelope, source: str, index: int):
        self.envelope = envelope
        self.source = source  # filename or "<inline:n>"
        self.index = index
        self.validation_errors: List[str] = []


_SOURCE_EXTENSIONS = (".pdf", ".xlsx", ".xlsm", ".xls", ".txt", ".docx")


def _candidate_source_names(doc: LoadedDocument) -> List[str]:
    env = doc.envelope
    names: List[Any] = [env.document.document_name, env.provenance.source_file]
    source_files = env.document.source_metadata.get("source_files", [])
    if isinstance(source_files, list):
        names.extend(item.get("source_file") for item in source_files
                     if isinstance(item, dict) and item.get("source_file"))
    if doc.source and not doc.source.startswith("<inline:"):
        names.append(Path(doc.source).stem)
    result: List[str] = []
    for value in names:
        if isinstance(value, list):
            result.extend(str(v) for v in value if v)
        elif isinstance(value, str) and value.strip():
            result.append(value.strip())
    return result


def _resolve_sibling_source(doc: LoadedDocument) -> Optional[Path]:
    """Resolve an extracted JSON file to its original sibling source."""
    if not doc.source or doc.source.startswith("<inline:"):
        return None
    json_path = Path(doc.source).resolve()
    if not json_path.is_file():
        return None
    folder = json_path.parent
    candidates: List[str] = []
    for name in _candidate_source_names(doc):
        # A composite document may append a form label after the physical name.
        leaf = Path(name.split(" :: ", 1)[0].strip()).name
        if Path(leaf).suffix.lower() in _SOURCE_EXTENSIONS:
            candidates.append(leaf)
        else:
            candidates.extend(leaf + ext for ext in _SOURCE_EXTENSIONS)
    # Extractor convention: JSON and original document share a stem.
    candidates.extend(json_path.stem + ext for ext in _SOURCE_EXTENSIONS)
    for name in candidates:
        candidate = (folder / name).resolve()
        if candidate.parent == folder and candidate.is_file() and candidate.suffix.lower() != ".json":
            return candidate
    # Extraction copies can have a numeric collision suffix; only compare
    # sibling names, and never traverse outside the account directory.
    wanted = {re.sub(r"\(\d+\)$", "", Path(n).stem).casefold()
              for n in candidates if n}
    for candidate in sorted(folder.iterdir()):
        if not candidate.is_file() or candidate.suffix.lower() not in _SOURCE_EXTENSIONS:
            continue
        stem = re.sub(r"\(\d+\)$", "", candidate.stem).casefold()
        if stem in wanted:
            return candidate
    return None


def load_json_file(path: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Returns (data, errors). Never raises on bad JSON."""
    errors: List[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"Cannot read {path}: {exc}"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"Invalid JSON in {path.name}: line {exc.lineno} col {exc.colno}: {exc.msg}"]
    if not isinstance(data, dict):
        return None, [f"{path.name}: top-level JSON must be an object"]
    return data, errors


def parse_envelope(data: Dict[str, Any], source: str) -> LoadedDocument:
    """Accept the preferred envelope or a bare data object."""
    if "data" in data and isinstance(data.get("data"), dict):
        try:
            envelope = InputEnvelope.model_validate(data)
        except ValidationError as exc:
            doc = LoadedDocument(InputEnvelope(data={}), source, 0)
            doc.validation_errors = [f"{source}: envelope validation failed: {exc}"]
            return doc
        return LoadedDocument(envelope, source, 0)
    # Bare document: wrap without inventing provenance.
    doc_meta = {}
    for key in ("document_id", "document_type", "document_name", "issuer",
                "version", "received_at", "source_hash"):
        if key in data and not isinstance(data[key], (dict, list)):
            doc_meta[key] = data.pop(key)
    envelope = InputEnvelope(
        schema_version=str(data.pop("schema_version", "1.0")),
        submission_id=data.pop("submission_id", None),
        document=doc_meta,
        data=data,
        provenance={"extraction_source": "bare_json", "source_file": source},
    )
    return LoadedDocument(envelope, source, 0)


def build_case(
    submission_id: str,
    run_id: str,
    loaded: List[LoadedDocument],
    line_of_business: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    effective_date: Optional[str] = None,
    review_date: Optional[str] = None,
) -> CanonicalCase:
    registry = get_registry()
    case = CanonicalCase(
        submission_id=submission_id, run_id=run_id,
        line_of_business=line_of_business, jurisdiction=jurisdiction,
        effective_date=effective_date, review_date=review_date,
    )
    used_ids: set[str] = set()
    for i, doc in enumerate(loaded):
        env = doc.envelope
        source_path = _resolve_sibling_source(doc)
        if source_path is not None:
            workspace_root = Path(__file__).resolve().parents[3]
            try:
                relative_source = source_path.relative_to(workspace_root).as_posix()
                relative_dir = source_path.parent.relative_to(workspace_root).as_posix()
            except ValueError:
                relative_source = relative_dir = None
            if relative_source:
                env.document.source_metadata["spectra_source_path"] = relative_source
                env.document.source_metadata["spectra_source_dir"] = relative_dir
                if (not env.document.document_name
                        or env.document.document_name.startswith("<inline:")
                        or Path(env.document.document_name).suffix.lower() == ".json"):
                    env.document.document_name = source_path.name
        if not env.document.document_name:
            env.document.document_name = f"Submission document {i + 1}"
        declared_type = env.document.document_type
        adapter = registry.adapter_for(declared_type, env.data)
        doc_id = env.document.document_id
        generated = False
        if not doc_id:
            base = env.document.document_name or Path(doc.source).stem or f"doc{i}"
            doc_id = f"GEN-{base}"
            generated = True
        # ensure uniqueness without overwriting supplied identity
        candidate, n = doc_id, 1
        while candidate in used_ids:
            n += 1
            candidate = f"{doc_id}#{n}"
            generated = True
        used_ids.add(candidate)
        adapted = adapter.adapt(env, candidate, generated)
        # Extractor JSON may carry an entire workbook as worksheet/cell
        # structures or OCR pages instead of normalized adapter fields. Keep
        # every such source available to GPT even when the typed adapter has
        # not yet learned that schema.
        if not adapted.evidence and env.data:
            from app.adapters.commercial import UnstructuredDocumentAdapter
            raw_adapted = UnstructuredDocumentAdapter().adapt(env, candidate, generated)
            adapted.evidence = raw_adapted.evidence
        all_adapted = [adapted] + list(adapted.extra_documents)
        for sub in all_adapted:
            sub.document.validation_errors = list(doc.validation_errors)
            sub_id = sub.document.document_id
            if not (sub is adapted and sub_id == candidate):
                # The primary document id was reserved before adaptation.
                n = 1
                while sub_id in used_ids:
                    n += 1
                    sub_id = f"{sub.document.document_id}#{n}"
                    sub.document.generated_id = True
                used_ids.add(sub_id)
            sub.document.document_id = sub_id
            case.documents.append(sub.document)
            if sub.document.content_hash:
                hash_evidence_id = f"EV-{sub_id}-CONTENT-HASH"
                case.evidence_store[hash_evidence_id] = EvidenceItem(
                    evidence_id=hash_evidence_id,
                    document_id=sub_id,
                    document_name=sub.document.document_name,
                    field_path="$document.content_hash",
                    source_location=SourceLocation(
                        json_path="$document.content_hash", available=False),
                    raw_value=sub.document.content_hash,
                    normalized_value=sub.document.content_hash,
                    snippet="SHA-256 of parsed document data",
                )
            for rec_list in (sub.entities, sub.locations, sub.vehicles, sub.drivers,
                             sub.claims, sub.wc_payroll_rows, sub.rating_factors,
                             sub.quote_terms, sub.exposures, sub.external_observations,
                             sub.checklist_items, sub.narratives):
                for record in rec_list:
                    record.document_id = sub_id
            for item in sub.evidence:
                item.document_id = sub_id
                case.evidence_store[item.evidence_id] = item
            case.entities.extend(sub.entities)
            case.locations.extend(sub.locations)
            case.vehicles.extend(sub.vehicles)
            case.drivers.extend(sub.drivers)
            case.claims.extend(sub.claims)
            case.wc_payroll_rows.extend(sub.wc_payroll_rows)
            case.rating_factors.extend(sub.rating_factors)
            case.quote_terms.extend(sub.quote_terms)
            case.exposures.extend(sub.exposures)
            case.external_observations.extend(sub.external_observations)
            case.checklist_items.extend(sub.checklist_items)
            case.narratives.extend(sub.narratives)
    return case
