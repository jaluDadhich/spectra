"""LLM-assisted exposure extraction with strict source-path validation."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.llm.gateway import ModelGateway
from app.llm.tracking import CostTracker
from app.normalization import normalize_money, parse_number
from app.schemas.canonical import CanonicalDocument, EvidenceItem, ExposureRecord, SourceLocation

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
EXPOSURE_TYPES = [
    "payroll", "sales", "square_footage", "vehicle_count",
    "cargo_value", "annual_claim_count", "operations",
]

EXTRACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string"},
                    "exposure_type": {"type": "string", "enum": EXPOSURE_TYPES},
                    "unit_path": {"type": ["string", "null"]},
                    "period_path": {"type": ["string", "null"]},
                    "location_id_path": {"type": ["string", "null"]},
                },
                "required": [
                    "source_path", "exposure_type", "unit_path",
                    "period_path", "location_id_path",
                ],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["proposals", "summary"],
    "additionalProperties": False,
}

JUDGE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "REJECT"]},
        "accepted_paths": {"type": "array", "items": {"type": "string"}},
        "issues": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["verdict", "accepted_paths", "issues", "summary"],
    "additionalProperties": False,
}


class ExposureProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    exposure_type: str
    unit_path: Optional[str]
    period_path: Optional[str]
    location_id_path: Optional[str]


class ProposalSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: List[ExposureProposal] = Field(default_factory=list)
    summary: str


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str
    accepted_paths: List[str]
    issues: List[str]
    summary: str


class ExtractionOutcome(BaseModel):
    status: str
    exposures: List[ExposureRecord] = Field(default_factory=list)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    proposed_count: int = 0
    rejected_count: int = 0
    judge_summary: Optional[str] = None


def _read_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()


def _schema_only(value: Any) -> Any:
    """Keep JSON shape and field names, but never send source values to an LLM."""
    if isinstance(value, dict):
        return {str(key): _schema_only(child) for key, child in value.items()}
    if isinstance(value, list):
        samples: List[Any] = []
        seen: set[str] = set()
        for child in value:
            sample = _schema_only(child)
            signature = json.dumps(sample, sort_keys=True, ensure_ascii=False)
            if signature not in seen:
                seen.add(signature)
                samples.append(sample)
                if len(samples) == 8:
                    break
        return samples
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        return "<boolean>"
    if isinstance(value, (int, float)):
        return "<number>"
    if isinstance(value, str):
        return "<string>"
    return f"<{type(value).__name__}>"


def _pointer_parts(pointer: str) -> Optional[List[str]]:
    if not pointer.startswith("/"):
        return None
    return [part.replace("~1", "/").replace("~0", "~")
            for part in pointer[1:].split("/")]


def resolve_pointer(data: Any, pointer: str) -> Tuple[bool, Any]:
    """Resolve an RFC 6901 JSON pointer against the document data object."""
    parts = _pointer_parts(pointer)
    if parts is None:
        return False, None
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def _encode_pointer_part(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def _expand_pointer(data: Any, pointer: str) -> List[Tuple[str, Any, Tuple[int, ...]]]:
    """Resolve a JSON pointer or a pointer template with ``*`` array segments."""
    parts = _pointer_parts(pointer)
    if parts is None:
        return []
    expanded: List[Tuple[str, Any, Tuple[int, ...]]] = []

    def walk(current: Any, index: int, resolved: List[str],
             wildcard_indexes: Tuple[int, ...]) -> None:
        if index == len(parts):
            exact = "/" + "/".join(_encode_pointer_part(part) for part in resolved)
            expanded.append((exact, current, wildcard_indexes))
            return
        part = parts[index]
        if part == "*" and isinstance(current, list):
            for item_index, item in enumerate(current):
                walk(item, index + 1, resolved + [str(item_index)],
                     wildcard_indexes + (item_index,))
        elif isinstance(current, dict) and part in current:
            walk(current[part], index + 1, resolved + [part], wildcard_indexes)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            walk(current[int(part)], index + 1, resolved + [part], wildcard_indexes)

    walk(data, 0, [], ())
    return expanded


def _concretize_pointer(pointer: Optional[str],
                        wildcard_indexes: Tuple[int, ...]) -> Optional[str]:
    if pointer is None:
        return None
    parts = _pointer_parts(pointer)
    if parts is None:
        return pointer
    next_index = 0
    concrete: List[str] = []
    for part in parts:
        if part == "*":
            if next_index >= len(wildcard_indexes):
                return None
            concrete.append(str(wildcard_indexes[next_index]))
            next_index += 1
        else:
            concrete.append(part)
    return "/" + "/".join(_encode_pointer_part(part) for part in concrete)


def _normalized_unit(exposure_type: str, unit: Optional[str],
                     raw_value: Any) -> Optional[str]:
    text = (unit or "").strip().casefold().replace(".", "")
    if exposure_type in {"payroll", "sales", "cargo_value"}:
        currencies = {
            "usd": "USD", "$": "USD", "us dollars": "USD", "dollars": "USD",
            "cad": "CAD", "canadian dollars": "CAD",
            "eur": "EUR", "€": "EUR", "gbp": "GBP", "£": "GBP",
        }
        currency = currencies.get(text)
        amount, symbol_currency = normalize_money(raw_value)
        if amount is None or currency is None:
            return None
        if isinstance(raw_value, str) and any(
                symbol in raw_value for symbol in ("$", "€", "£")):
            if symbol_currency != currency:
                return None
        return currency
    if exposure_type == "square_footage":
        return "sqft" if text in {"sqft", "sq ft", "square feet", "square foot"} else None
    if exposure_type in {"vehicle_count", "annual_claim_count"}:
        valid = {"count", "vehicle", "vehicles"} if exposure_type == "vehicle_count" \
            else {"count", "claim", "claims"}
        if not text:
            return "count"
        return "count" if text in valid else None
    if exposure_type == "operations":
        return "text" if not text or text == "text" else None
    return None


def _scalar(data: Any, pointer: Optional[str]) -> Any:
    if not pointer:
        return None
    exists, value = resolve_pointer(data, pointer)
    if not exists or isinstance(value, (dict, list, bool)) or value is None:
        return None
    return value


def _canonical_value(exposure_type: str, raw: Any) -> Optional[Any]:
    if exposure_type == "operations":
        return raw.strip() if isinstance(raw, str) and raw.strip() else None
    number = parse_number(raw)
    if number is None or not math.isfinite(number):
        return None
    if exposure_type in {"square_footage", "vehicle_count", "annual_claim_count"}:
        if number < 0 or (exposure_type != "square_footage" and not number.is_integer()):
            return None
        return int(number) if number.is_integer() else number
    if exposure_type in {"payroll", "sales", "cargo_value"}:
        return number if number >= 0 else None
    return None


def _make_proposal(document: CanonicalDocument, data: Any,
                   proposal: ExposureProposal, index: int
                   ) -> Tuple[Optional[ExposureRecord], Optional[EvidenceItem], Optional[str]]:
    if proposal.exposure_type not in EXPOSURE_TYPES:
        return None, None, f"Unsupported exposure type: {proposal.exposure_type}"
    exists, raw = resolve_pointer(data, proposal.source_path)
    if not exists or isinstance(raw, (dict, list, bool)) or raw is None:
        return None, None, f"Source path is missing or not a scalar: {proposal.source_path}"
    value = _canonical_value(proposal.exposure_type, raw)
    if value is None:
        return None, None, f"Source value is not valid for {proposal.exposure_type}: {proposal.source_path}"
    raw_unit = _scalar(data, proposal.unit_path)
    unit_text = str(raw_unit).strip() if raw_unit is not None else None
    unit = _normalized_unit(proposal.exposure_type, unit_text, raw)
    if unit is None:
        return None, None, f"Invalid or unsupported unit for {proposal.source_path}"

    period_value = _scalar(data, proposal.period_path)
    period = str(period_value).strip() if period_value is not None else None
    location_value = _scalar(data, proposal.location_id_path)
    location_id = str(location_value).strip() if location_value is not None else None
    if proposal.exposure_type == "annual_claim_count" and not period:
        return None, None, f"Annual claim count has no source-backed period: {proposal.source_path}"

    record_id = f"{document.document_id}#llm-exposure-{index}"
    evidence_id = f"EV-{document.document_id}-LLM-{index:04d}"
    evidence = EvidenceItem(
        evidence_id=evidence_id,
        document_id=document.document_id,
        document_name=document.document_name,
        field_path=f"exposures[{record_id}].value",
        source_location=SourceLocation(json_path=proposal.source_path, available=True),
        raw_value=raw,
        normalized_value=value,
        unit=unit,
        period=period,
        snippet=str(raw)[:160],
    )
    exposure = ExposureRecord(
        record_id=record_id,
        document_id=document.document_id,
        field_evidence={"value": evidence_id},
        exposure_type=proposal.exposure_type,
        value=value,
        unit=unit,
        period=period,
        location_id=location_id,
        description=proposal.source_path.rsplit("/", 1)[-1],
    )
    return exposure, evidence, None


def extract_document_exposures(
    gateway: ModelGateway,
    tracker: CostTracker,
    settings: Settings,
    document: CanonicalDocument,
) -> ExtractionOutcome:
    """Extract, validate, and judge one document without trusting model values.

    The model proposes source paths and semantic labels only. Values are
    always read from the original JSON by the deterministic validator.
    """
    outcome = ExtractionOutcome(status="NOT_ATTEMPTED")
    if not document.raw:
        outcome.status = "NO_SOURCE_DATA"
        return outcome
    schema_json = json.dumps(_schema_only(document.raw), ensure_ascii=False)
    if len(schema_json) > settings.spectra_max_evidence_payload_chars:
        outcome.status = "INCOMPLETE"
        outcome.errors.append(
            f"Document {document.document_id} exceeds the exposure extraction payload limit")
        return outcome
    payload = json.dumps({"data_schema": _schema_only(document.raw)}, ensure_ascii=False)

    try:
        tracker.check_budget()
        response = gateway.complete_json(
            system_prompt=_read_prompt("exposure_extraction_system.md"),
            user_prompt=_read_prompt("exposure_extraction.md").replace("{{ source }}", payload),
            json_schema=EXTRACTION_SCHEMA,
            schema_name="exposure_extraction",
        )
        tracker.record(response.input_tokens, response.output_tokens,
                       retries=response.retries, model=response.model, cost_tier="reasoning")
    except Exception as exc:
        outcome.status = "INCOMPLETE"
        outcome.errors.append(f"Exposure extraction unavailable for {document.document_id}: {exc}")
        return outcome

    try:
        proposals = ProposalSet.model_validate(response.content)
    except ValidationError as exc:
        outcome.status = "INCOMPLETE"
        outcome.errors.append(f"Exposure extraction schema validation failed: {exc}")
        return outcome

    outcome.proposed_count = len(proposals.proposals)
    verified: List[Tuple[ExposureProposal, ExposureRecord, EvidenceItem]] = []
    seen_paths: set[str] = set()
    for proposal in proposals.proposals:
        if proposal.source_path in seen_paths:
            outcome.rejected_count += 1
            outcome.errors.append(f"Duplicate exposure source path rejected: {proposal.source_path}")
            continue
        seen_paths.add(proposal.source_path)
        expanded = _expand_pointer(document.raw, proposal.source_path)
        if not expanded:
            outcome.rejected_count += 1
            outcome.errors.append(
                f"Source path does not resolve in the input: {proposal.source_path}")
            continue
        for exact_path, _, wildcard_indexes in expanded:
            concrete = proposal.model_copy(update={
                "source_path": exact_path,
                "unit_path": _concretize_pointer(proposal.unit_path, wildcard_indexes),
                "period_path": _concretize_pointer(proposal.period_path, wildcard_indexes),
                "location_id_path": _concretize_pointer(
                    proposal.location_id_path, wildcard_indexes),
            })
            if any(path is None for path in (
                    concrete.unit_path if proposal.unit_path else "",
                    concrete.period_path if proposal.period_path else "",
                    concrete.location_id_path if proposal.location_id_path else "",
            )):
                outcome.rejected_count += 1
                outcome.errors.append(
                    f"Metadata path does not align with source array: {proposal.source_path}")
                continue
            exposure, evidence, error = _make_proposal(
                document, document.raw, concrete, len(verified) + 1)
            if error:
                outcome.rejected_count += 1
                outcome.errors.append(error)
                continue
            if exposure is not None and evidence is not None:
                verified.append((proposal, exposure, evidence))

    if outcome.rejected_count:
        outcome.status = "INCOMPLETE"
        return outcome
    if not verified:
        outcome.status = "NO_EXPOSURES"
        return outcome

    judge_input = {
        "source_schema": _schema_only(document.raw),
        "verified_proposals": [{
            "source_path": proposal.source_path,
            "exposure_type": proposal.exposure_type,
            "unit_path": proposal.unit_path,
            "period_path": proposal.period_path,
            "location_id_path": proposal.location_id_path,
        } for proposal in {item[0].source_path: item[0] for item in verified}.values()],
    }
    try:
        tracker.check_budget()
        judge_response = gateway.complete_json(
            system_prompt=_read_prompt("exposure_judge_system.md"),
            user_prompt=_read_prompt("exposure_judge.md").replace(
                "{{ model }}", json.dumps(judge_input, ensure_ascii=False, default=str)),
            json_schema=JUDGE_SCHEMA,
            schema_name="exposure_model_judge",
        )
        tracker.record(judge_response.input_tokens, judge_response.output_tokens,
                       retries=judge_response.retries, model=judge_response.model,
                       cost_tier="reasoning")
        judge = JudgeResult.model_validate(judge_response.content)
    except Exception as exc:
        outcome.status = "INCOMPLETE"
        outcome.errors.append(f"Exposure model judge unavailable or invalid: {exc}")
        return outcome

    expected_paths = {proposal.source_path for proposal, _, _ in verified}
    if judge.verdict != "PASS" or set(judge.accepted_paths) != expected_paths:
        outcome.status = "REJECTED_BY_JUDGE"
        outcome.errors.extend(judge.issues)
        outcome.errors.append(
            "Exposure judge did not approve exactly the deterministically verified source paths")
        outcome.judge_summary = judge.summary
        return outcome

    outcome.status = "ACCEPTED"
    outcome.judge_summary = judge.summary
    outcome.exposures = [exposure for _, exposure, _ in verified]
    outcome.evidence = [evidence for _, _, evidence in verified]
    return outcome
