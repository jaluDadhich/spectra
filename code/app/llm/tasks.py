"""Bounded GPT-assisted semantic review tasks (spec Stage 9 / section 7).

Tasks are narrow and evidence-bundled. Every candidate is validated
against the evidence store: hallucinated citations, invented values, or
invalid enum values cause the candidate to be rejected or downgraded.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from app.catalog.issue_register import load_register
from app.config import Settings
from app.findings.builder import FindingBuilder, FindingDraft
from app.llm.gateway import ModelGateway, estimate_request_tokens
from app.llm.tracking import CostTracker
from app.scoring.quality import quality_issue_groups
from app.schemas.canonical import CanonicalCase
from app.schemas.findings import (
    ConclusionState,
    Finding,
    FindingClassification,
    MaterialityBand,
    ConfidenceBasis,
    IssueNature,
    Severity,
    UncertaintyState,
)

logger = logging.getLogger("spectra.llm.tasks")

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_VERSION = {"base_system": "1.2.0", "semantic_review": "1.4.0",
                  "scope_adjudication": "1.5.0",
                  "explain_finding": "1.1.0",
                  "exposure_extraction": "1.0.0",
                  "exposure_model_judge": "1.0.0"}

# The unified account-agnostic issue register (U01-U14) is the complete and
# exclusive issue scope for GPT adjudication.
ISSUE_IDS = [f"U{n:02d}" for n in range(1, 15)]
ISSUE_NATURES = [item.value for item in IssueNature]
SEVERITIES = [item.value for item in Severity]
SCOPE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "finding_id": {"type": "string"},
                "disposition": {"type": "string", "enum": ["KEEP", "DROP"]},
                "issue_id": {"type": "string", "enum": ISSUE_IDS + ["OUT_OF_SCOPE"]},
                "issue_nature": {"type": "string", "enum": ISSUE_NATURES},
                "issue_group_key": {"type": "string", "maxLength": 64},
                "issue_group_title": {"type": "string", "maxLength": 100},
                "severity": {"type": "string", "enum": SEVERITIES},
                "rationale": {"type": "string", "maxLength": 300},
                "severity_rationale": {"type": "string", "maxLength": 220},
                "calculation_rationale": {"type": "string", "maxLength": 300},
            },
            "required": ["finding_id", "disposition", "issue_id", "issue_nature",
                         "issue_group_key", "issue_group_title",
                         "severity", "rationale", "severity_rationale",
                         "calculation_rationale"],
            "additionalProperties": False,
        }},
        "additional_candidates": {"type": "array", "maxItems": 1, "items": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "enum": ISSUE_IDS},
                "candidate_type": {"type": "string"},
                "issue_nature": {"type": "string", "enum": ISSUE_NATURES},
                "issue_group_key": {"type": "string", "maxLength": 64},
                "issue_group_title": {"type": "string", "maxLength": 100},
                "title": {"type": "string", "maxLength": 180},
                "observed_facts": {"type": "array", "maxItems": 5,
                                   "items": {"type": "string", "maxLength": 300}},
                "comparison_summary": {"type": "string", "maxLength": 500},
                "evidence_ids": {"type": "array", "maxItems": 8,
                                 "items": {"type": "string"}},
                "possible_consequence": {"type": "string", "maxLength": 300},
                "recommended_action": {"type": "string", "maxLength": 300},
                "uncertainty_state": {"type": "string", "enum": [
                    "CONFIRMED_DATA_CONFLICT", "POSSIBLE_MISMATCH",
                    "EVIDENCE_GAP", "NEEDS_HUMAN_REVIEW"]},
                "limitations": {"type": "array", "maxItems": 5,
                                "items": {"type": "string", "maxLength": 240}},
                "severity": {"type": "string", "enum": SEVERITIES},
                "severity_rationale": {"type": "string", "maxLength": 220},
            },
            "required": ["issue_id", "candidate_type", "issue_nature",
                         "issue_group_key", "issue_group_title", "title", "observed_facts",
                         "comparison_summary", "evidence_ids", "possible_consequence",
                         "recommended_action", "uncertainty_state", "limitations",
                         "severity", "severity_rationale"],
            "additionalProperties": False,
        }},
        "summary": {"type": "string"},
    },
    "required": ["decisions", "additional_candidates", "summary"],
    "additionalProperties": False,
}


def _valid_issue_group(key: Any, title: Any) -> bool:
    if not isinstance(key, str) or not isinstance(title, str):
        return False
    key_text = key.strip().lower()
    title_text = title.strip()
    return (bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", key_text))
            and 3 <= len(title_text) <= 100
            and not any(ord(char) < 32 for char in title_text))

CANDIDATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "candidate_findings": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "candidate_type": {"type": "string"},
                    "issue_nature": {"type": "string", "enum": ISSUE_NATURES},
                    "title": {"type": "string", "maxLength": 180},
                    "observed_facts": {"type": "array", "maxItems": 5,
                                       "items": {"type": "string", "maxLength": 300}},
                    "comparison_summary": {"type": "string", "maxLength": 500},
                    "evidence_ids": {"type": "array", "maxItems": 8,
                                      "items": {"type": "string"}},
                    "possible_consequence": {"type": "string"},
                    "recommended_action": {"type": "string"},
                    "uncertainty_state": {"type": "string", "enum": [
                        "CONFIRMED_DATA_CONFLICT", "POSSIBLE_MISMATCH",
                        "EVIDENCE_GAP", "NEEDS_HUMAN_REVIEW"]},
                    "limitations": {"type": "array", "maxItems": 5,
                                    "items": {"type": "string", "maxLength": 240}},
                },
                "required": ["candidate_type", "issue_nature", "title", "observed_facts",
                             "comparison_summary", "evidence_ids",
                             "possible_consequence", "recommended_action",
                             "uncertainty_state", "limitations"],
                "additionalProperties": False,
            },
        },
        "no_issue_reason": {"type": ["string", "null"]},
    },
    "required": ["task_id", "candidate_findings", "no_issue_reason"],
    "additionalProperties": False,
}

EXPLANATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"explanation": {"type": "string"}},
    "required": ["explanation"],
    "additionalProperties": False,
}

QUALITY_SEVERITY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"issue_groups": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "group_key": {"type": "string"},
            "points_lost": {"type": "number"},
            "rationale": {"type": "string"},
        },
        "required": ["group_key", "points_lost", "rationale"],
        "additionalProperties": False,
    }}},
    "required": ["issue_groups"],
    "additionalProperties": False,
}


class CandidateFinding(BaseModel):
    candidate_type: str
    issue_nature: IssueNature
    title: str = Field(max_length=180)
    observed_facts: List[str] = Field(max_length=5)
    comparison_summary: str = Field(max_length=500)
    evidence_ids: List[str] = Field(max_length=8)
    possible_consequence: str
    recommended_action: str
    uncertainty_state: UncertaintyState
    limitations: List[str] = Field(default_factory=list, max_length=5)


class CandidateSet(BaseModel):
    task_id: str
    candidate_findings: List[CandidateFinding] = Field(max_length=5)
    no_issue_reason: Optional[str] = None


def load_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    # strip front matter
    return re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL).strip()


def render(template: str, **values: Any) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{ " + key + " }}", str(value))
    return out


class SemanticReviewer:
    def __init__(self, gateway: ModelGateway, settings: Settings, tracker: CostTracker):
        self.gateway = gateway
        self.settings = settings
        self.tracker = tracker
        self.last_call_succeeded = False

    def _bundle(self, case: CanonicalCase, evidence_ids: List[str],
                limit: Optional[int] = None, preserve_order: bool = False,
                max_value_chars: Optional[int] = None,
                max_snippet_chars: Optional[int] = None) -> str:
        by_document: Dict[str, List[str]] = {}
        ordered_lines: List[str] = []
        total = 0
        limit = limit or self.settings.spectra_max_evidence_payload_chars
        for eid in evidence_ids:
            item = case.evidence_store.get(eid)
            if item is None:
                continue
            value = item.normalized_value
            if max_value_chars is not None and isinstance(value, str) and len(value) > max_value_chars:
                value = value[:max_value_chars] + " … [candidate evidence excerpt]"
            snippet = item.snippet
            if max_snippet_chars is not None and isinstance(snippet, str) and len(snippet) > max_snippet_chars:
                snippet = snippet[:max_snippet_chars] + " … [evidence excerpt]"
            line = json.dumps({
                "evidence_id": item.evidence_id,
                "document": item.document_id,
                "document_name": item.document_name,
                "field": item.field_path,
                "source_location": item.source_location.model_dump(exclude_none=True),
                "value": value,
                "unit": item.unit,
                "period": item.period,
                "snippet": snippet,
            }, default=str)
            ordered_lines.append(line)
            by_document.setdefault(item.document_id, []).append(line)
        if not preserve_order:
            # A large document must not consume the entire context window and
            # hide every other submission file. Round-robin by source document
            # so each one contributes evidence before extra rows are included.
            ordered_lines = []
            max_items = max((len(items) for items in by_document.values()), default=0)
            for index in range(max_items):
                for items in by_document.values():
                    if index < len(items):
                        ordered_lines.append(items[index])
        lines: List[str] = []
        for line in ordered_lines:
            if total + len(line) > limit:
                lines.append("... [bundle truncated by size budget]")
                break
            total += len(line)
            lines.append(line)
        return "\n".join(lines) or "(no evidence supplied)"

    def review(self, case: CanonicalCase, task_id: str, question: str,
               allowed_families: List[str], evidence_ids: List[str],
               rule_refs: Optional[List[str]] = None,
               schema_name: str = "spectra_candidates",
               reviewer_memory: Optional[List[Dict[str, Any]]] = None) -> tuple[List[Finding], List[str]]:
        """Returns (accepted findings, rejection notes)."""
        self.last_call_succeeded = False
        notes: List[str] = []
        builder = FindingBuilder(case)
        memory_context = [{
            key: item.get(key) for key in ("taxonomy_id", "finding_type", "action", "rationale")
            if item.get(key) is not None
        } for item in (reviewer_memory or [])]
        register_data = load_register(self.settings.spectra_ruleset_dir).model_dump()
        allowed_issue_ids = {
            match.group(0)
            for family in allowed_families
            for match in [re.search(r"U\d{2}", family)]
            if match
        }
        if allowed_issue_ids:
            register_data["issues"] = [
                item for item in register_data.get("issues", [])
                if item.get("id") in allowed_issue_ids
            ]
        evidence_bundle = self._bundle(case, evidence_ids)
        bundled_evidence_ids = set()
        for line in evidence_bundle.splitlines():
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(item, dict) and item.get("evidence_id"):
                bundled_evidence_ids.add(str(item["evidence_id"]))
        try:
            self.tracker.check_budget()
            response = self.gateway.complete_json(
                system_prompt=load_prompt("base_system"),
                user_prompt=render(
                    load_prompt("semantic_review"),
                    submission_id=case.submission_id, task_id=task_id,
                    allowed_families=", ".join(allowed_families),
                    question=question,
                    rule_refs=", ".join(rule_refs or ["none configured"]),
                    reviewer_memory=json.dumps(memory_context, default=str),
                    unified_issue_register=json.dumps(
                        register_data, ensure_ascii=False, default=str),
                    evidence_bundle=evidence_bundle),
                json_schema=CANDIDATE_SCHEMA, schema_name=schema_name)
            self.tracker.record(response.input_tokens, response.output_tokens,
                                retries=response.retries, model=response.model,
                                cost_tier=("fast" if schema_name in {
                                    "document_triage", "finding_explanation"}
                                           else "reasoning"))
        except Exception as exc:
            notes.append(f"Semantic task {task_id} unavailable: {exc}")
            return [], notes

        try:
            candidates = CandidateSet.model_validate(response.content)
        except ValidationError as exc:
            notes.append(f"Semantic task {task_id} output failed schema validation: {exc}")
            return [], notes

        # A valid empty result is a completed GPT review, not a call failure.
        self.last_call_succeeded = True

        accepted: List[Finding] = []
        for cand in candidates.candidate_findings:
            missing = [e for e in cand.evidence_ids
                       if e not in case.evidence_store or e not in bundled_evidence_ids]
            if missing:
                notes.append(f"Candidate '{cand.title}' rejected: nonexistent evidence IDs {missing}")
                continue
            if not cand.evidence_ids:
                notes.append(f"Candidate '{cand.title}' rejected: no evidence cited")
                continue
            conclusion = {
                UncertaintyState.CONFIRMED_DATA_CONFLICT: ConclusionState.POSSIBLE_MISMATCH,
                UncertaintyState.POSSIBLE_MISMATCH: ConclusionState.POSSIBLE_MISMATCH,
                UncertaintyState.EVIDENCE_GAP: ConclusionState.EVIDENCE_GAP,
                UncertaintyState.NEEDS_HUMAN_REVIEW: ConclusionState.POSSIBLE_MISMATCH,
            }[cand.uncertainty_state]
            # Model-suggested candidates are never auto-confirmed.
            draft = FindingDraft(
                finding_type=f"GPT_{cand.candidate_type}",
                taxonomy_id="GPT",
                rule_id="GPT-SEMANTIC-001",
                rule_version=PROMPT_VERSION["semantic_review"],
                title=cand.title,
                issue_nature=cand.issue_nature,
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=conclusion,
                severity=Severity.MEDIUM,
                materiality_band=MaterialityBand.UNKNOWN,
                materiality_rationale="Model-suggested candidate; materiality requires human or "
                                      "deterministic confirmation",
                confidence_basis=ConfidenceBasis.MODEL_SUGGESTED,
                confidence_explanation="Model confidence is not a calibrated probability",
                uncertainty_reason="; ".join(cand.limitations) or cand.uncertainty_state.value,
                evidence_ids=cand.evidence_ids,
                possible_consequence=cand.possible_consequence,
                recommended_action=cand.recommended_action,
                deduplication_key=f"GPT:{task_id}:{cand.title[:64]}")
            finding = builder.build(draft)
            if finding is None:
                notes.append(f"Candidate '{cand.title}' rejected at build: invalid evidence")
                continue
            accepted.append(finding)
        # Invalid candidate citations reject that candidate only. The model
        # call itself succeeded and other evidence-valid candidates remain
        # usable; deterministic findings still proceed to the scope auditor.
        return accepted, notes

    def adjudicate_scope(self, case: CanonicalCase, findings: List[Finding],
                         catalog: Dict[str, Any]) -> tuple[List[Finding], List[str]]:
        """Judge every candidate in bounded, citation-complete batches.

        A single strict-JSON answer for a large submission can exceed model
        output limits. Batching keeps each judgment complete. If the remaining
        per-case call budget cannot cover every batch, the caller uses the
        deterministic fallback for the whole run.
        """
        self.last_call_succeeded = False
        notes: List[str] = []
        issue_by_id = {item["id"]: item for item in catalog.get("issues", [])}
        if not issue_by_id:
            return [], ["Issue-scope catalog is empty"]
        decision_map: Dict[str, Dict[str, Any]] = {}
        additions: List[Dict[str, Any]] = []
        candidate_by_id = {finding.finding_id: finding for finding in findings}
        system_prompt = load_prompt("base_system")
        scope_prompt = load_prompt("scope_adjudication")

        def representative_refs(finding: Finding, limit: int = 8) -> List[Any]:
            """Keep a bounded, spread sample of citations for the scope judge.

            The complete citations remain on the stored finding. The judge gets
            enough references to validate the pattern without allowing a single
            schedule with dozens of near-identical rows to consume the request.
            """
            refs = list(finding.evidence)
            if len(refs) <= limit:
                return refs
            indexes = sorted({round(index * (len(refs) - 1) / (limit - 1))
                              for index in range(limit)})
            return [refs[index] for index in indexes]

        def compact_json(value: Any, max_chars: int) -> str:
            if value is None:
                return ""
            rendered = json.dumps(value, ensure_ascii=False, default=str,
                                  separators=(",", ":"))
            return rendered if len(rendered) <= max_chars else rendered[:max_chars - 1] + "…"

        def prepare_batch(batch: List[Finding]) -> Dict[str, Any]:
            candidate_payload = [{
                "finding_id": f.finding_id,
                "title": f.title[:700],
                "finding_type": f.finding_type[:100],
                "rule_id": f.rule_id[:100],
                "taxonomy_id": f.taxonomy_id,
                "classification": f.classification.value,
                "conclusion_state": f.conclusion_state.value,
                "issue_nature": f.issue_nature.value if f.issue_nature else None,
                "severity": f.severity.value,
                "severity_rationale": (f.severity_rationale or "")[:300],
                "uncertainty_reason": (f.uncertainty_reason or "")[:300],
                "evidence_ids": [e.evidence_id for e in representative_refs(f)],
                "values_compared": compact_json(
                    (f.comparison.values_compared[:8] if f.comparison else []), 1200),
                "source_comparison": compact_json(
                    ([source.model_dump(mode="json") for source in f.comparison.sources[:4]]
                     if f.comparison else []), 1800),
                "calculation": compact_json(
                    (f.comparison.calculation.model_dump(mode="json")
                     if f.comparison and f.comparison.calculation else None), 1200),
                "materiality": compact_json(f.materiality.model_dump(mode="json"), 800),
                "possible_consequence": (f.possible_consequence or "")[:300],
                "recommended_action": (f.recommended_action or "")[:300],
            } for f in batch]
            question = (
                "Issue catalog (the only allowed issue scope): "
                + json.dumps(catalog, ensure_ascii=False, default=str)
                + "\nJudge only the candidate IDs in this batch; keep group labels "
                  "stable for the same issue family across batches.\nCandidates: "
                + json.dumps(candidate_payload, ensure_ascii=False, default=str)
            )
            candidate_evidence_ids = list(dict.fromkeys(
                evidence_id for finding in batch
                for evidence_id in (ref.evidence_id for ref in representative_refs(finding))))
            evidence_bundle = self._bundle(
                case, candidate_evidence_ids,
                limit=self.settings.spectra_scope_evidence_payload_chars,
                preserve_order=True, max_value_chars=250,
                max_snippet_chars=250)
            supplied_evidence_ids = set()
            for line in evidence_bundle.splitlines():
                try:
                    evidence_item = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(evidence_item, dict) and evidence_item.get("evidence_id"):
                    supplied_evidence_ids.add(evidence_item["evidence_id"])
            if len(supplied_evidence_ids) != len(candidate_evidence_ids):
                return {"findings": batch, "fits": False,
                        "reason": "citation evidence exceeds the evidence bundle limit"}
            user_prompt = render(
                scope_prompt, submission_id=case.submission_id,
                evidence_bundle=evidence_bundle, question=question)
            estimated_input = estimate_request_tokens(
                system_prompt, user_prompt, SCOPE_SCHEMA)
            # Reserve enough structured-output room for every candidate,
            # including rationale/group fields and a small set of discoveries.
            # Scope decisions carry several structured fields per finding.
            # Reserve enough space for complete adjudications; a tight reserve
            # can leave the gateway with a clipped completion allowance even
            # when the actual prompt is smaller than the conservative estimate.
            output_reserve = min(
                self.settings.openai_scope_max_output_tokens,
                1_000 + 800 * len(batch))
            fits = (estimated_input + output_reserve
                    <= self.settings.openai_max_request_tokens)
            return {
                "findings": batch,
                "evidence_bundle": evidence_bundle,
                "supplied_evidence_ids": supplied_evidence_ids,
                "user_prompt": user_prompt,
                "estimated_input_tokens": estimated_input,
                "output_reserve_tokens": output_reserve,
                "fits": fits,
                "reason": ("estimated input plus reserved completion exceeds the "
                           "per-request token limit" if not fits else ""),
            }

        # Greedily pack up to three findings at a time while respecting the same
        # conservative token estimator and completion reserve as the gateway.
        # A finding and all of its citations always stay together.
        # Scope decisions include long severity and calculation rationales.
        # Smaller batches leave more completion room for strict structured
        # outputs and reduce the chance that one large answer aborts the full
        # adjudication. Token packing may still produce smaller batches.
        batch_size = 3
        batch_specs: List[Dict[str, Any]] = []
        current: List[Finding] = []
        for finding in findings:
            if not finding.evidence:
                return [], [f"Scope candidate {finding.finding_id} has no evidence citations"]
            trial = current + [finding]
            trial_spec = prepare_batch(trial) if len(trial) <= batch_size else {"fits": False}
            if trial_spec.get("fits"):
                current = trial
                continue
            if current:
                batch_specs.append(prepare_batch(current))
                current = []
            single_spec = prepare_batch([finding])
            if not single_spec.get("fits"):
                return [], [f"Scope candidate {finding.finding_id} cannot fit safely in one "
                            f"request: {single_spec.get('reason', 'token budget exceeded')}"]
            current = [finding]
        if current:
            batch_specs.append(prepare_batch(current))

        remaining_calls = max(0, self.tracker.max_calls - self.tracker.usage.calls)
        if len(batch_specs) > remaining_calls:
            return [], [f"Scope audit needs {len(batch_specs)} token-bounded judge calls but only "
                        f"{remaining_calls} remain in the per-case call budget"]

        for batch_index, batch_spec in enumerate(batch_specs, start=1):
            batch = batch_spec["findings"]
            evidence_bundle = batch_spec["evidence_bundle"]
            supplied_evidence_ids = batch_spec["supplied_evidence_ids"]
            logger.info(
                "event=scope_batch_ready batch=%d/%d candidates=%d evidence=%d "
                "estimated_input_tokens=%d reserved_output_tokens=%d request_limit=%d",
                batch_index, len(batch_specs), len(batch),
                len(supplied_evidence_ids), batch_spec["estimated_input_tokens"],
                batch_spec["output_reserve_tokens"],
                self.settings.openai_max_request_tokens)
            try:
                self.tracker.check_budget()
                response = self.gateway.complete_json(
                    system_prompt=system_prompt,
                    user_prompt=batch_spec["user_prompt"],
                    json_schema=SCOPE_SCHEMA, schema_name="scope_adjudication")
                self.tracker.record(response.input_tokens, response.output_tokens,
                                    retries=response.retries, model=response.model,
                                    cost_tier="reasoning")
            except Exception as exc:
                return [], [f"Scope adjudication batch {batch_index}/{len(batch_specs)} "
                            f"unavailable: {exc}"]

            content = response.content
            decisions = content.get("decisions") if isinstance(content, dict) else None
            expected_ids = {finding.finding_id for finding in batch}
            if not isinstance(decisions, list):
                return [], [f"Scope batch {batch_index} returned no decisions list"]
            batch_decisions: Dict[str, Dict[str, Any]] = {}

            def validate_scope_decision(decision: Any, allowed_ids: set[str]
                                        ) -> tuple[Optional[str], Optional[str]]:
                fid = decision.get("finding_id") if isinstance(decision, dict) else None
                if fid not in allowed_ids:
                    return None, "unknown finding ID"
                if decision.get("disposition") not in {"KEEP", "DROP"}:
                    return None, f"invalid disposition for {fid}"
                if decision.get("issue_id") not in issue_by_id | {"OUT_OF_SCOPE": None}:
                    return None, f"unknown issue ID for {fid}"
                if (decision.get("disposition") == "KEEP"
                        and decision.get("issue_id") == "OUT_OF_SCOPE"):
                    return None, f"out-of-scope finding kept for {fid}"
                if decision.get("issue_nature") not in ISSUE_NATURES:
                    return None, f"invalid issue nature for {fid}"
                if not _valid_issue_group(decision.get("issue_group_key"),
                                          decision.get("issue_group_title")):
                    return None, f"invalid issue group for {fid}"
                if decision.get("severity") not in SEVERITIES:
                    return None, f"invalid severity for {fid}"
                if not str(decision.get("severity_rationale") or "").strip():
                    return None, f"missing severity rationale for {fid}"
                finding = candidate_by_id[fid]
                calculation_rationale = str(decision.get("calculation_rationale") or "").strip()
                if (finding.comparison and finding.comparison.calculation
                        and (not calculation_rationale
                             or calculation_rationale.casefold() == "not applicable")):
                    return None, f"missing experience-mod rationale for {fid}"
                return fid, None

            for decision in decisions:
                fid, validation_error = validate_scope_decision(decision, expected_ids)
                if validation_error:
                    return [], [f"Scope adjudication batch {batch_index} {validation_error}"]
                if fid in batch_decisions:
                    return [], [f"Scope batch {batch_index} returned duplicate finding ID {fid}"]
                batch_decisions[fid] = decision

            missing_ids = expected_ids - set(batch_decisions)
            repair_attempt = 0
            max_repair_attempts = 3
            while missing_ids and repair_attempt < max_repair_attempts:
                repair_attempt += 1
                missing_summary = sorted(missing_ids)
                logger.warning(
                    "event=scope_batch_retry_missing_decisions batch=%d attempt=%d missing=%d",
                    batch_index, repair_attempt, len(missing_ids))
                repair_prompt = batch_spec["user_prompt"] + (
                    "\n\nREPAIR REQUEST: Your prior response omitted a required decision. "
                    "Return a decision for every one of these missing finding_id values, "
                    "and no others: " + json.dumps(missing_summary)
                    + ". Re-evaluate only those candidates from the supplied evidence and "
                      "issue catalog. Include every required field and rationale. "
                      "Set additional_candidates to an empty array.")
                try:
                    self.tracker.check_budget()
                    repair_response = self.gateway.complete_json(
                        system_prompt=system_prompt,
                        user_prompt=repair_prompt,
                        json_schema=SCOPE_SCHEMA, schema_name="scope_adjudication")
                    self.tracker.record(
                        repair_response.input_tokens, repair_response.output_tokens,
                        retries=repair_response.retries, model=repair_response.model,
                        cost_tier="reasoning")
                except Exception as exc:
                    return [], [f"Scope adjudication repair {repair_attempt} for batch "
                                f"{batch_index} unavailable: {exc}"]
                repair_content = repair_response.content
                repair_decisions = (repair_content.get("decisions")
                                    if isinstance(repair_content, dict) else None)
                if not isinstance(repair_decisions, list):
                    return [], [f"Scope adjudication repair for batch {batch_index} "
                                "returned no decisions list"]
                for decision in repair_decisions:
                    fid = decision.get("finding_id") if isinstance(decision, dict) else None
                    if fid in batch_decisions:
                        continue
                    fid, validation_error = validate_scope_decision(decision, missing_ids)
                    if validation_error:
                        return [], [f"Scope adjudication repair for batch {batch_index} "
                                    f"{validation_error}"]
                    batch_decisions[fid] = decision
                missing_ids = expected_ids - set(batch_decisions)
            if missing_ids:
                still_missing = sorted(missing_ids)
                return [], [f"Scope batch {batch_index} still lacks decisions for "
                            f"{still_missing} after {repair_attempt} repair attempt(s)"]

            batch_additions = content.get("additional_candidates", [])
            if not isinstance(batch_additions, list):
                return [], [f"Scope batch {batch_index} returned an invalid additions list"]
            for item in batch_additions:
                if not isinstance(item, dict) or item.get("issue_id") not in issue_by_id:
                    return [], [f"Scope batch {batch_index} returned an out-of-catalog added issue"]
                if (not item.get("evidence_ids") or any(
                        eid not in case.evidence_store or eid not in supplied_evidence_ids
                        for eid in item.get("evidence_ids", []))):
                    return [], [f"Added issue '{item.get('title', '')}' cites missing evidence"]
                if item.get("issue_nature") not in ISSUE_NATURES:
                    return [], [f"Added issue '{item.get('title', '')}' has invalid issue nature"]
                if not _valid_issue_group(item.get("issue_group_key"),
                                          item.get("issue_group_title")):
                    return [], [f"Added issue '{item.get('title', '')}' has invalid issue group"]
                if (item.get("severity") not in SEVERITIES
                        or not str(item.get("severity_rationale") or "").strip()):
                    return [], [f"Added issue '{item.get('title', '')}' has invalid severity"]
            decision_map.update(batch_decisions)
            additions.extend(batch_additions)
            if content.get("summary"):
                notes.append(str(content["summary"]))

        expected_all_ids = set(candidate_by_id)
        if set(decision_map) != expected_all_ids:
            return [], ["Scope adjudication did not decide every candidate"]

        self.last_call_succeeded = True
        scoped: List[Finding] = []
        for finding in findings:
            decision = decision_map[finding.finding_id]
            issue_id = decision["issue_id"]
            if (decision["disposition"] == "DROP"
                    or decision["severity"] == Severity.INFORMATIONAL.value):
                continue
            finding.taxonomy_id = issue_id
            finding.issue_nature = IssueNature(decision["issue_nature"])
            finding.issue_group_key = decision["issue_group_key"].strip().lower()
            finding.issue_group_title = decision["issue_group_title"].strip()
            finding.severity = Severity(decision["severity"])
            finding.severity_rationale = str(decision["severity_rationale"]).strip()
            calculation_rationale = str(decision.get("calculation_rationale") or "").strip()
            if (finding.comparison and finding.comparison.calculation
                    and calculation_rationale
                    and calculation_rationale.lower() != "not applicable"):
                finding.comparison.calculation.rationale = calculation_rationale
            if finding.conclusion_state == ConclusionState.EVIDENCE_GAP:
                finding.classification = FindingClassification.EVIDENCE_GAP
            else:
                finding.classification = FindingClassification.NON_MANIFESTED_DISCOVERY
            finding.uncertainty_reason = decision.get("rationale") or finding.uncertainty_reason
            scoped.append(finding)

        builder = FindingBuilder(case)
        for item in additions:
            if item["severity"] == Severity.INFORMATIONAL.value:
                continue
            issue_id = item["issue_id"]
            added_ids = set(item["evidence_ids"])
            duplicate = False
            for current in scoped:
                if current.taxonomy_id != issue_id:
                    continue
                current_ids = {e.evidence_id for e in current.evidence}
                if added_ids and current_ids and (
                        added_ids.issubset(current_ids) or current_ids.issubset(added_ids)):
                    duplicate = True
                    break
            if duplicate:
                continue
            conclusion = {
                UncertaintyState.CONFIRMED_DATA_CONFLICT: ConclusionState.CONFIRMED_DATA_CONFLICT,
                UncertaintyState.POSSIBLE_MISMATCH: ConclusionState.POSSIBLE_MISMATCH,
                UncertaintyState.EVIDENCE_GAP: ConclusionState.EVIDENCE_GAP,
                UncertaintyState.NEEDS_HUMAN_REVIEW: ConclusionState.POSSIBLE_MISMATCH,
            }[UncertaintyState(item["uncertainty_state"])]
            classification = (FindingClassification.EVIDENCE_GAP
                              if conclusion == ConclusionState.EVIDENCE_GAP
                              else FindingClassification.NON_MANIFESTED_DISCOVERY)
            draft = FindingDraft(
                finding_type=f"GPT_{item['candidate_type']}", taxonomy_id=issue_id,
                rule_id="GPT-SCOPE-001", rule_version=PROMPT_VERSION["scope_adjudication"],
                title=item["title"], classification=classification,
                conclusion_state=conclusion, severity=Severity(item["severity"]),
                severity_rationale=item["severity_rationale"],
                issue_nature=IssueNature(item["issue_nature"]),
                materiality_band=MaterialityBand.UNKNOWN,
                materiality_rationale="Scope-auditor discovery; materiality requires review",
                confidence_basis=ConfidenceBasis.MODEL_SUGGESTED,
                confidence_explanation="Model confidence is not a calibrated probability",
                uncertainty_reason="; ".join(item.get("limitations", [])) or item["uncertainty_state"],
                evidence_ids=item["evidence_ids"],
                possible_consequence=item["possible_consequence"],
                recommended_action=item["recommended_action"],
                deduplication_key=f"GPT-SCOPE:{issue_id}:{item['title'][:64]}")
            finding = builder.build(draft)
            if finding is None:
                self.last_call_succeeded = False
                return [], [f"Added issue '{item['title']}' failed evidence validation"]
            finding.issue_group_key = item["issue_group_key"].strip().lower()
            finding.issue_group_title = item["issue_group_title"].strip()
            scoped.append(finding)
        return scoped, notes

    def explain(self, finding: Finding) -> Optional[str]:
        """Explain a validated finding; never re-decides the result."""
        try:
            self.tracker.check_budget()
            payload = finding.model_dump(
                include={"title", "finding_type", "issue_nature", "conclusion_state",
                         "severity", "severity_rationale",
                         "possible_consequence", "recommended_action",
                         "uncertainty_reason", "comparison"},
                exclude_none=True)
            payload["evidence"] = [
                {"evidence_id": e.evidence_id, "document": e.document_id,
                 "value": e.normalized_value, "unit": e.unit}
                for e in finding.evidence]
            response = self.gateway.complete_json(
                system_prompt=load_prompt("base_system"),
                user_prompt=render(load_prompt("explain_finding"),
                                   finding_json=json.dumps(payload, default=str)),
                json_schema=EXPLANATION_SCHEMA, schema_name="finding_explanation")
            self.tracker.record(response.input_tokens, response.output_tokens,
                                retries=response.retries, model=response.model,
                                cost_tier="fast")
            text = str(response.content.get("explanation", "")).strip()
            return text or None
        except Exception:
            logger.exception("event=finding_explanation_failed finding_id=%s",
                             finding.finding_id)
            return None

    def score_issue_severity(self, findings: List[Finding]
                             ) -> tuple[Optional[Dict[str, Dict[str, Any]]], List[str]]:
        """Assign one low-cost, reasoned points deduction per distinct issue family."""
        groups = quality_issue_groups(findings)
        if not groups:
            return {}, []
        logger.info("event=quality_issue_scoring_started groups=%d model_tier=fast",
                    len(groups))
        payload = [{
            "group_key": group["group_key"],
            "issue_group": group["title"],
            "finding_count": group["finding_count"],
            "finding_titles": group["titles"][:5],
            "severities": group["severities"],
            "issue_natures": sorted(set(group["issue_natures"])),
            "conclusion_states": sorted(set(group["conclusions"])),
            "materiality_bands": sorted(set(group["materiality_bands"])),
            "severity_rationales": group["rationales"][:3],
            "uncertainty_notes": group["uncertainties"][:3],
        } for group in groups]
        try:
            self.tracker.check_budget()
            response = self.gateway.complete_json(
                system_prompt=load_prompt("base_system"),
                user_prompt=render(
                    load_prompt("quality_issue_scoring"),
                    issue_groups=json.dumps(payload, ensure_ascii=False, default=str)),
                json_schema=QUALITY_SEVERITY_SCHEMA,
                schema_name="quality_severity_scoring")
            self.tracker.record(response.input_tokens, response.output_tokens,
                                retries=response.retries, model=response.model,
                                cost_tier="fast")
        except Exception as exc:
            logger.warning("event=quality_issue_scoring_unavailable error_type=%s",
                           type(exc).__name__)
            return None, [f"LLM issue-severity scoring unavailable: {type(exc).__name__}"]

        values = response.content.get("issue_groups") if isinstance(response.content, dict) else None
        expected = {group["group_key"] for group in groups}
        if not isinstance(values, list):
            return None, ["LLM issue-severity scoring returned no issue groups"]
        scores: Dict[str, Dict[str, Any]] = {}
        for value in values:
            if not isinstance(value, dict):
                return None, ["LLM issue-severity scoring returned a malformed group"]
            key = value.get("group_key")
            try:
                points = float(value.get("points_lost"))
            except (TypeError, ValueError):
                return None, [f"LLM issue-severity scoring returned invalid points for {key}"]
            rationale = str(value.get("rationale") or "").strip()
            if (key not in expected or key in scores or not 0 <= points <= 10
                    or not rationale):
                return None, [f"LLM issue-severity scoring returned an invalid decision for {key}"]
            scores[key] = {"points_lost": points, "rationale": rationale}
        if set(scores) != expected:
            return None, ["LLM issue-severity scoring did not score every unique issue group"]
        logger.info("event=quality_issue_scoring_completed groups=%d points_lost=%.1f",
                    len(scores), sum(value["points_lost"] for value in scores.values()))
        return scores, []
