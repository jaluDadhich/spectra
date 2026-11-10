"""Staged workflow: GPT-first whole-submission review, with deterministic fallback."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.adapters.loader import (
    LoadedDocument, build_case, load_json_file, parse_envelope,
    submission_input_paths,
)
from app.catalog.profiles import DocumentProfile, load_profile
from app.config import Settings, get_settings
from app.external_sources import build_source_registry
from app.findings import compute_case_status, deduplicate, rank_findings
from app.llm import CostTracker, MockGateway, SemanticReviewer, get_gateway
from app.llm.gateway import ModelGateway
from app.llm.tasks import PROMPT_VERSION
from app.logging_config import configure_logging
from app.normalization import normalize_entity_name
from app.rules import RuleEngine, load_registry
from app.schemas.findings import CaseStatus, Finding
from app.schemas.envelope import InputEnvelope
from app.schemas.review import (
    CheckOutcome,
    CheckStatus,
    ReviewRequest,
    ReviewResult,
    StageResult,
    StageStatus,
    TokenUsage,
)
from app.storage import CaseRepository

logger = logging.getLogger("spectra.orchestrator")

# Rule families mapped to workflow stages for reporting.
_STAGE_OF_RULE = {
    "DOC-INVENTORY": 1, "P1-": 1,
    "DQ-DUP": 3, "DQ-ARITH": 3, "DQ-DATES": 3, "DQ-STALE": 3,
    "P2-": 4, "P5-": 4, "P7-": 4, "N3-": 4, "N4-": 4, "N5-": 4,
    "N7-": 4, "N14-": 4, "N6-": 4,
    "P3-": 6, "P6-": 6, "N15-": 6,
    "P11-": 7,
    "P4-": 8,
}

_STAGE_NAMES = {
    0: "case_initialization", 1: "document_inventory", 2: "normalization",
    3: "within_document_validation", 4: "cross_document_reconciliation",
    5: "external_truth_checks", 6: "pricing_input_validation",
    7: "exposure_coverage_fit", 8: "document_integrity_signals",
    9: "gpt_semantic_review", 10: "dedup_severity_prioritization",
    11: "findings_synthesis",
}

# Deterministic rule-to-category mappings for the unified account-agnostic
# issue register (U01-U14). Used when GPT is disabled or unavailable; GPT
# adjudication performs the broader semantic mapping against the same
# register. Rules not listed here (document inventory, generic data
# quality) are infrastructure checks and never surface as issue findings.
def _deterministic_scope_filter(findings: List[Finding]) -> List[Finding]:
    """Keep deterministic findings that map to the unified U01-U14 register.

    Any finding whose rule does not map to a U-category is out of scope and
    is dropped: the register is the complete and exclusive issue taxonomy.
    """
    from app.catalog.issue_register import load_register

    try:
        register = load_register()
    except Exception:
        # The filter is a security boundary for the issue taxonomy. If its
        # catalog is unavailable or malformed, fail closed instead of leaking
        # unclassified findings or crashing the review worker.
        logger.exception("event=issue_register_unavailable fallback_scope=fail_closed")
        return []
    issue_ids = set(register.issue_ids)
    # The register is the source of truth for fallback mappings too; do not
    # maintain a second, drifting taxonomy in Python.
    rule_to_issue = {
        rule_id: category.id
        for category in register.issues
        for rule_id in category.rule_ids
    }
    scoped: List[Finding] = []
    for finding in findings:
        issue_id = rule_to_issue.get(finding.rule_id)
        doc_text = " ".join(
            [finding.finding_type, finding.title]
            + [f"{e.document_id} {e.document_name or ''}" for e in finding.evidence]
        ).casefold()
        # Generic duplicate-ID / arithmetic handlers belong to U08 only when
        # they specifically concern loss-run claims; otherwise they are
        # data-quality infrastructure and stay out of the issue register.
        if finding.rule_id in {"DQ-DUP-ID-001", "DQ-ARITH-001"} and "loss" in doc_text:
            issue_id = "U08"
        if not issue_id or issue_id not in issue_ids:
            continue
        finding.taxonomy_id = issue_id
        if finding.conclusion_state.value == "EVIDENCE_GAP":
            finding.classification = finding.classification.__class__.EVIDENCE_GAP
        else:
            finding.classification = finding.classification.__class__.NON_MANIFESTED_DISCOVERY
        scoped.append(finding)
    return scoped


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Orchestrator:
    def __init__(self, settings: Optional[Settings] = None,
                 repository: Optional[CaseRepository] = None,
                 gateway: Optional[ModelGateway] = None):
        self.settings = settings or get_settings()
        self.repository = repository
        self._gateway_override = gateway

    # ------------------------------------------------------------------
    def run_review(self, request: ReviewRequest,
                   progress_cb=None,
                   run_id: Optional[str] = None) -> ReviewResult:
        configure_logging(self.settings)
        run_id = run_id or f"RUN-{uuid.uuid4().hex[:12].upper()}"
        run_started = time.monotonic()
        logger.info("event=review_started run_id=%s account=%s mode=%s lob=%s input_dir=%s",
                    run_id, request.submission_id,
                    "deterministic" if request.deterministic_only else "gpt_assisted",
                    request.line_of_business, request.input_dir or "inline")
        result = ReviewResult(
            run_id=run_id, submission_id=request.submission_id,
            status=CaseStatus.ERROR_INCOMPLETE, created_at=_now())
        stages: Dict[int, StageResult] = {
            n: StageResult(stage=name, status=StageStatus.PENDING)
            for n, name in _STAGE_NAMES.items()
        }

        def emit(stage_no: int) -> None:
            stage = stages[stage_no]
            elapsed = None
            if stage.started_at and stage.finished_at:
                try:
                    started_at = datetime.fromisoformat(stage.started_at)
                    finished_at = datetime.fromisoformat(stage.finished_at)
                    elapsed = max(0.0, (finished_at - started_at).total_seconds())
                except ValueError:
                    pass
            logger.info("event=pipeline_stage run_id=%s stage=%s status=%s elapsed_seconds=%s checks=%d findings=%d errors=%d",
                        run_id, stage.stage, stage.status.value,
                        f"{elapsed:.3f}" if elapsed is not None else "unknown",
                        len(stage.checks), sum(len(check.finding_ids) for check in stage.checks),
                        len(stage.errors))
            if progress_cb is not None:
                try:
                    progress_cb(run_id, stage_no, stage.stage, stage.status.value)
                except Exception:  # progress reporting must never break a run
                    logger.debug("Progress callback failed for %s", run_id,
                                 exc_info=True)

        input_hash = ""
        try:
            case, tracker = self._stages_0_to_2(request, run_id, stages, result)
            emit(0)
            emit(1)
            emit(2)
            if case is None:
                result.stages = list(stages.values())
                result.completed_at = _now()
                return result
            input_hash = self._input_hash(request, result.config_versions)
            semantic_review_completed = False
            if request.deterministic_only:
                findings, checks, rejected = self._run_deterministic_fallback(
                    case, stages, result)
                findings = _deterministic_scope_filter(findings)
                stages[9].status = StageStatus.SKIPPED
                stages[9].metrics = {"reason": "deterministic-only mode requested"}
                for n in range(3, 10):
                    emit(n)
            else:
                logger.info("event=gpt_workflow_started run_id=%s", run_id)
                findings, checks = self._stage_9(case, stages[9], request, tracker, result)
                rejected = []
                if stages[9].metrics.get("deterministic_fallback"):
                    findings, fallback_checks, rejected = self._run_deterministic_fallback(
                        case, stages, result)
                    findings = _deterministic_scope_filter(findings)
                    checks.extend(fallback_checks)
                    for n in range(3, 10):
                        emit(n)
                else:
                    semantic_review_completed = True
                    for stage_no in range(3, 9):
                        stage = stages[stage_no]
                        stage.status = StageStatus.SKIPPED
                        stage.started_at = stage.finished_at = _now()
                        stage.metrics = {
                            "reason": "LangGraph agents handled this stage; "
                                     "deterministic checks remain the failure fallback"}
                        if stage_no == 5:
                            stage.metrics["reason"] = (
                                "Local-first research tool handled external evidence; "
                                "no separate direct-source adapter ran")
                        emit(stage_no)
                    emit(9)
            checks.extend(stages[1].checks)
            findings = self._stage_10(findings, stages[10])
            emit(10)
            result.findings = findings
            result.checks = checks
            result.errors.extend(rejected)
            result.stages = list(stages.values())  # needed by quality scoring
            self._stage_11(result, stages[11], case, semantic_review_completed, tracker)
            emit(11)
            result.token_usage = tracker.usage
            logger.info("event=review_findings_synthesized run_id=%s findings=%d llm_calls=%d",
                        run_id, len(findings), tracker.usage.calls)
        except Exception as exc:  # workflow-level failure
            logger.exception("Review run %s failed", run_id)
            result.errors.append(f"{type(exc).__name__}: {exc}")
            for stage in stages.values():
                if stage.status in {StageStatus.PENDING, StageStatus.RUNNING}:
                    stage.status = StageStatus.FAILED
                    stage.errors.append(str(exc))
            result.status = CaseStatus.ERROR_INCOMPLETE
        result.partial = self._is_partial(result)
        result.stages = list(stages.values())
        result.completed_at = _now()
        if result.quality_score is None:
            from app.scoring import score_submission
            result.quality_score = score_submission(result)
        if self.repository:
            self.repository.save_run(result, input_hash)
            self.repository.audit("review_completed", run_id=run_id,
                                  detail=f"status={result.status.value} "
                                         f"findings={len(result.findings)}")
        logger.info("event=review_finished run_id=%s account=%s status=%s partial=%s findings=%d llm_calls=%d elapsed_seconds=%.3f",
                    run_id, request.submission_id, result.status.value,
                    result.partial, len(result.findings), result.token_usage.calls,
                    time.monotonic() - run_started)
        return result

    # ------------------------------------------------------------------
    def _stages_0_to_2(self, request: ReviewRequest, run_id: str,
                       stages: Dict[int, StageResult],
                       result: ReviewResult) -> Tuple[Optional[object], CostTracker]:
        tracker = CostTracker(
            max_calls=self.settings.spectra_max_llm_calls_per_case,
            budget_usd=self.settings.spectra_llm_budget_usd_per_case,
            price_in_per_1k=self.settings.spectra_price_input_per_1k,
            price_out_per_1k=self.settings.spectra_price_output_per_1k,
            reasoning_price_in_per_1k=self.settings.spectra_reasoning_price_input_per_1k,
            reasoning_price_out_per_1k=self.settings.spectra_reasoning_price_output_per_1k)
        # STAGE 0
        stage = stages[0]
        stage.status = StageStatus.RUNNING
        stage.started_at = _now()
        try:
            profile: Optional[DocumentProfile] = load_profile(
                request.profile or "commercial_package_default")
            registry = load_registry(request.ruleset or "default")
            from app.catalog.issue_register import load_register
            register = load_register(self.settings.spectra_ruleset_dir)
        except Exception as exc:
            stage.status = StageStatus.FAILED
            stage.errors.append(str(exc))
            result.errors.append(f"Configuration load failed: {exc}")
            stage.finished_at = _now()
            return None, tracker
        result.config_versions = {
            "profile": f"{profile.profile_id}@{profile.version}",
            "ruleset": f"{registry.ruleset.ruleset_id}@{registry.ruleset.version}",
            "prompts": ";".join(f"{k}@{v}" for k, v in PROMPT_VERSION.items()),
            "exposure_model": "canonical-exposures@1.0.0",
            "agent_workflow": "langgraph-review@1.2.0",
            "issue_scope": f"{register.catalog_id}@{register.version}",
            "scope_evidence_limit": str(self.settings.spectra_scope_evidence_payload_chars),
            "external_research": "local-first-search@1.0.0",
            "models": (f"fast={self.settings.openai_fast_model or self.settings.openai_model}"
                       f"(effort={self.settings.openai_fast_reasoning_effort});"
                       f"reasoning={self.settings.openai_reasoning_model or self.settings.openai_model}"
                       f"(effort={self.settings.openai_reasoning_effort})"
                       if self.settings.llm_available else "disabled"),
        }
        self._profile, self._registry = profile, registry
        self._register = register
        stage.status = StageStatus.COMPLETED
        stage.finished_at = _now()

        # STAGE 1 — load + validate + inventory
        stage = stages[1]
        stage.status = StageStatus.RUNNING
        stage.started_at = _now()
        loaded, load_errors = self._load_documents(request)
        logger.info("event=document_inventory run_id=%s loaded=%d errors=%d",
                    run_id, len(loaded), len(load_errors))
        if load_errors:
            stage.errors.extend(load_errors)
            result.errors.extend(load_errors)
        if not loaded:
            stage.status = StageStatus.FAILED
            stage.errors.append("No usable input documents")
            stage.finished_at = _now()
            result.errors.append("No usable input documents")
            return None, tracker
        if any(d.validation_errors for d in loaded):
            for d in loaded:
                stage.errors.extend(d.validation_errors)
            if not request.allow_partial:
                stage.status = StageStatus.FAILED
                stage.finished_at = _now()
                result.errors.append("Invalid inputs and allow_partial=false")
                return None, tracker
            stage.status = StageStatus.INCOMPLETE
        case = build_case(
            submission_id=request.submission_id, run_id=run_id, loaded=loaded,
            line_of_business=request.line_of_business,
            jurisdiction=request.jurisdiction,
            effective_date=request.effective_date, review_date=request.review_date)
        unsupported_documents = [doc for doc in case.documents if not doc.recognized]
        if unsupported_documents:
            names = ", ".join(
                f"{doc.document_name or doc.document_id} "
                f"({doc.declared_document_type or doc.document_type})"
                for doc in unsupported_documents)
            message = f"Unsupported document type(s) preserved but not analyzed: {names}"
            stage.errors.append(message)
            result.errors.append(message)
            stage.status = StageStatus.INCOMPLETE
            stage.checks.append(CheckOutcome(
                check_id="UNSUPPORTED-DOCUMENT-TYPES",
                status=CheckStatus.INCOMPLETE,
                detail=message))
        extraction_status: Dict[str, str] = {}
        self._review_gateway = None
        if not request.deterministic_only:
            self._review_gateway = self._gateway_override or get_gateway(self.settings)
            eligible_types = {"unsupported", "unstructured_document", "excel_workbook"}
            documents_to_extract = [
                document for document in case.documents
                if document.document_type in eligible_types
                and not any(exposure.document_id == document.document_id
                            for exposure in case.exposures)
            ]
            if documents_to_extract and self._review_gateway is not None:
                from app.extraction.llm_extractor import extract_document_exposures

                for document in documents_to_extract:
                    outcome = extract_document_exposures(
                        self._review_gateway, tracker, self.settings, document)
                    extraction_status[document.document_id] = outcome.status
                    case.exposures.extend(outcome.exposures)
                    case.evidence_store.update(
                        {item.evidence_id: item for item in outcome.evidence})
                    if outcome.errors:
                        stage = stages[2]
                        stage.errors.extend(outcome.errors)
                        result.errors.extend(outcome.errors)
            elif documents_to_extract:
                extraction_status.update({
                    document.document_id: "NOT_ATTEMPTED"
                    for document in documents_to_extract
                })
                missing_model_errors = [
                    f"Exposure extraction was not run for unstructured document "
                    f"{document.document_id}; no validated model is available"
                    for document in documents_to_extract
                ]
                stages[2].errors.extend(missing_model_errors)
                result.errors.extend(missing_model_errors)

        from app.extraction.store import save_exposure_model
        try:
            save_exposure_model(
                self.settings.spectra_data_dir, case,
                extraction_status=extraction_status)
        except OSError as exc:
            message = f"Could not persist exposure model: {exc}"
            stages[2].errors.append(message)
            result.errors.append(message)

        insured = next((e for e in case.entities if e.entity_role == "insured"), None)
        identity = normalize_entity_name(
            (insured.legal_name or insured.normalized_name) if insured else None)
        if identity:
            result.config_versions["account_memory_key"] = hashlib.sha256(
                identity.encode("utf-8")).hexdigest()[:24]
        stage.metrics = {
            "documents_loaded": len(loaded),
            "recognized": sum(1 for d in case.documents if d.recognized),
            "unsupported": sum(1 for d in case.documents if not d.recognized),
        }
        logger.info("event=case_normalized run_id=%s documents=%d evidence=%d claims=%d locations=%d vehicles=%d drivers=%d payroll_rows=%d exposures=%d",
                    run_id, len(loaded), len(case.evidence_store), len(case.claims),
                    len(case.locations), len(case.vehicles), len(case.drivers),
                    len(case.wc_payroll_rows), len(case.exposures))
        if stage.status != StageStatus.INCOMPLETE:
            stage.status = StageStatus.COMPLETED
        stage.finished_at = _now()

        # STAGE 2 — normalization happened inside adapters; record metrics
        stage = stages[2]
        stage.status = StageStatus.INCOMPLETE if stage.errors else StageStatus.COMPLETED
        stage.started_at = stage.finished_at = _now()
        evidence_by_document = Counter(
            item.document_id for item in case.evidence_store.values())
        stage.metrics = {
            "evidence_items": len(case.evidence_store),
            "claims": len(case.claims), "locations": len(case.locations),
            "vehicles": len(case.vehicles), "drivers": len(case.drivers),
            "wc_rows": len(case.wc_payroll_rows),
            "quote_terms": len(case.quote_terms),
            "exposures": len(case.exposures),
            "llm_extraction_status": extraction_status,
            "exposure_model_persisted": not any(
                error.startswith("Could not persist exposure model:")
                for error in stages[2].errors),
            "document_evidence": {
                doc.document_name or doc.document_id: evidence_by_document.get(doc.document_id, 0)
                for doc in case.documents
            },
            "documents_without_evidence": [
                doc.document_name or doc.document_id for doc in case.documents
                if evidence_by_document.get(doc.document_id, 0) == 0
            ],
        }
        self._case = case
        return case, tracker

    def _stages_3_to_8(self, case, stages, result) -> Tuple[List[Finding], List[CheckOutcome], List[str]]:
        engine = RuleEngine(self._registry, self._profile)
        findings, checks, rejected = engine.run(case)
        logger.info("event=deterministic_rules_completed run_id=%s rules=%d candidates=%d rejected=%d",
                    case.run_id, len(checks), len(findings), len(rejected))
        per_stage: Dict[int, List[CheckOutcome]] = {n: [] for n in (1, 3, 4, 6, 7, 8)}
        for check in checks:
            stage_no = 4
            for prefix, n in _STAGE_OF_RULE.items():
                if check.check_id.startswith(prefix):
                    stage_no = n
                    break
            per_stage.setdefault(stage_no, []).append(check)
        for n, stage_checks in per_stage.items():
            if n == 1:
                stages[1].checks.extend(stage_checks)
                continue
            stage = stages[n]
            stage.started_at = stage.finished_at = _now()
            stage.checks = stage_checks
            stage.status = StageStatus.COMPLETED
            stage.metrics = {"findings": sum(len(c.finding_ids) for c in stage_checks)}
        return findings, checks, rejected

    def _run_deterministic_fallback(self, case, stages, result):
        """Run the established deterministic checks when GPT is unavailable."""
        findings, checks, rejected = self._stages_3_to_8(case, stages, result)
        checks.extend(self._stage_5(case, stages[5]))
        return findings, checks, rejected

    def _stage_5(self, case, stage: StageResult) -> List[CheckOutcome]:
        stage.status = StageStatus.RUNNING
        stage.started_at = _now()
        checks: List[CheckOutcome] = []
        registry = build_source_registry(self.settings)
        enabled = registry.enabled()
        if not enabled:
            stage.status = StageStatus.SKIPPED
            stage.finished_at = _now()
            stage.metrics = {"reason": "No approved external sources configured (disabled by default)"}
            checks.append(CheckOutcome(
                check_id="EXT-SOURCES", status=CheckStatus.SKIPPED_NOT_APPLICABLE,
                detail="External verification disabled: no approved source registry/credentials"))
            return checks
        # Enabled adapters: bundled registry ships none; approved adapters
        # would run here and produce outcome states.
        for source_id in enabled:
            adapter = registry.adapter_for(source_id)
            outcome = adapter.lookup({})
            checks.append(CheckOutcome(
                check_id=f"EXT-{source_id}",
                status=CheckStatus.COMPLETED if outcome.outcome.value != "UNAVAILABLE_OR_STALE"
                else CheckStatus.INCOMPLETE,
                detail=f"{outcome.outcome.value}: {outcome.match_rationale}"))
        stage.status = StageStatus.COMPLETED
        stage.finished_at = _now()
        return checks

    def _stage_9(self, case, stage: StageResult,
                 request: ReviewRequest, tracker: CostTracker,
                 result: ReviewResult):
        stage.status = StageStatus.RUNNING
        stage.started_at = _now()
        checks: List[CheckOutcome] = []
        gateway = (getattr(self, "_review_gateway", None)
                   or self._gateway_override
                   or get_gateway(self.settings, deterministic_only=request.deterministic_only))
        if gateway is None:
            reason = ("deterministic-only mode requested" if request.deterministic_only
                      else "LLM disabled or OPENAI_API_KEY not configured")
            stage.status = StageStatus.SKIPPED
            stage.finished_at = _now()
            stage.metrics = {"reason": reason, "deterministic_fallback": True}
            checks.append(CheckOutcome(
                check_id="GPT-WHOLE-SUBMISSION", status=CheckStatus.COMPLETED,
                detail=f"GPT unavailable; deterministic review used ({reason})"))
            return [], checks
        reviewer = SemanticReviewer(gateway, self.settings, tracker)
        from app.agents.langgraph_workflow import run_agent_graph
        deterministic_candidates: List[Finding] = []
        try:
            # Deterministic rules are candidate generators only in the GPT
            # path. The LangGraph scope judge decides whether each candidate
            # is in scope and worth surfacing, assigns severity, and can add
            # its own evidence-backed findings. No rule result is final here.
            from app.rules.engine import RuleEngine
            deterministic_candidates, _, _ = RuleEngine(
                self._registry, self._profile).run(case)
            logger.info("event=deterministic_candidate_generation run_id=%s candidates=%d",
                        case.run_id, len(deterministic_candidates))
        except Exception:
            logger.exception("Deterministic candidate generation failed for %s", case.run_id)
        try:
            workflow = run_agent_graph(
                case, reviewer, self.settings, self.repository, case.run_id,
                result.config_versions.get("account_memory_key"),
                seed_findings=deterministic_candidates)
        except Exception as exc:
            logger.exception("LangGraph review workflow failed for %s", case.run_id)
            workflow = {"findings": [], "trace": [{
                "node": "langgraph", "status": "failed",
                "error": f"{type(exc).__name__}: {exc}"}],
                "gpt_failed": True, "failure_reason": str(exc)}
        accepted = workflow["findings"]
        trace = workflow["trace"]
        stage.metrics = {"workflow": "langgraph", "agent_trace": trace,
                         "agents_run": sum(1 for e in trace if e.get("status") == "completed"),
                         "external_live_calls": sum(e.get("live_calls", 0) for e in trace),
                         "external_cache_hits": sum(e.get("cache_hits", 0) for e in trace)}
        research = next((event for event in trace
                         if event.get("node") == "external_research_tool"), None)
        if research and research.get("status") in {"error", "partial_live_search"}:
            checks.append(CheckOutcome(
                check_id="EXT-WEB-SEARCH", status=CheckStatus.INCOMPLETE,
                detail="; ".join(research.get("errors", [])) or
                       research.get("error", "One or more configured searches failed")))
        if workflow["gpt_failed"]:
            reason = workflow.get("failure_reason") or "One or more GPT agents failed"
            stage.status = StageStatus.INCOMPLETE
            stage.finished_at = _now()
            stage.metrics["reason"] = reason
            stage.metrics["deterministic_fallback"] = True
            logger.error("event=gpt_workflow_failed run_id=%s reason=%s fallback=deterministic",
                         case.run_id, reason)
            checks.append(CheckOutcome(
                check_id="LANGGRAPH-AGENTS", status=CheckStatus.INCOMPLETE,
                detail=f"Agent graph failed; deterministic review used ({reason})"))
            return [], checks
        checks.append(CheckOutcome(
            check_id="LANGGRAPH-AGENTS", status=CheckStatus.COMPLETED,
            detail=f"Ran {stage.metrics['agents_run']} agents; accepted "
                   f"{len(accepted)} candidate(s)",
            finding_ids=[f.finding_id for f in accepted]))
        logger.info("event=gpt_workflow_completed run_id=%s agents=%d accepted=%d candidates=%d llm_calls=%d",
                    case.run_id, stage.metrics["agents_run"], len(accepted),
                    len(deterministic_candidates), tracker.usage.calls)
        # Short explanations use the configured lightweight model.
        for finding in accepted[:3]:
            explanation = reviewer.explain(finding)
            if explanation:
                finding.explanation = explanation
        stage.status = StageStatus.COMPLETED
        stage.finished_at = _now()
        return accepted, checks

    def _stage_10(self, findings, stage: StageResult) -> List[Finding]:
        stage.status = StageStatus.RUNNING
        stage.started_at = _now()
        merged = deduplicate(findings)
        ranked = rank_findings(merged)
        stage.status = StageStatus.COMPLETED
        stage.finished_at = _now()
        stage.metrics = {"input": len(findings), "after_dedup": len(ranked)}
        return ranked

    def _stage_11(self, result: ReviewResult, stage: StageResult, case=None,
                  semantic_review_completed: bool = False,
                  tracker: Optional[CostTracker] = None) -> None:
        stage.status = StageStatus.RUNNING
        stage.started_at = _now()
        if case is not None and getattr(self, "_register", None) is not None:
            from app.findings.category_status import derive_category_assessments
            result.category_assessments = derive_category_assessments(
                case, self._register, result.findings, result.checks,
                semantic_review_completed=semantic_review_completed)
            stage.metrics["category_statuses"] = {
                a.issue_id: a.status.value for a in result.category_assessments}
        result.status = compute_case_status(result.findings, result.checks)
        from app.scoring import score_submission
        issue_severity_points = None
        scoring_calls_before = tracker.usage.calls if tracker is not None else 0
        if semantic_review_completed and self._review_gateway is not None and tracker is not None:
            scorer = SemanticReviewer(self._review_gateway, self.settings, tracker)
            issue_severity_points, notes = scorer.score_issue_severity(result.findings)
            stage.metrics["issue_severity_scoring"] = {
                "model_tier": "fast",
                "status": "completed" if issue_severity_points is not None else "fallback",
                "unique_issue_groups": len(issue_severity_points or {}),
                "calls": tracker.usage.calls - scoring_calls_before,
                "notes": notes[:2],
            }
        result.quality_score = score_submission(
            result, issue_severity_points=issue_severity_points)
        stage.metrics["quality_score"] = result.quality_score.score
        stage.metrics["quality_grade"] = result.quality_score.grade
        stage.status = StageStatus.COMPLETED
        stage.finished_at = _now()

    # ------------------------------------------------------------------
    def _load_documents(self, request: ReviewRequest) -> Tuple[List[LoadedDocument], List[str]]:
        loaded: List[LoadedDocument] = []
        errors: List[str] = []
        paths: List[Path] = []
        workspace_root = Path(__file__).resolve().parents[3]
        if request.input_dir:
            root = Path(request.input_dir).resolve()
            allowed_roots = [workspace_root, Path(self.settings.accounts_dir).resolve()]
            if not any(root == allowed or root.is_relative_to(allowed)
                       for allowed in allowed_roots):
                errors.append(f"input_dir outside allowed roots: {root}")
            elif not root.is_dir():
                errors.append(f"input_dir not found: {root}")
            else:
                paths.extend(submission_input_paths(root))
        for doc in request.documents:
            if doc.path:
                p = Path(doc.path).resolve()
                try:
                    p.relative_to(workspace_root)
                except ValueError:
                    errors.append(f"Path outside allowed workspace rejected: {p}")
                    continue
                paths.append(p)
            elif doc.content is not None:
                parsed = parse_envelope(dict(doc.content), f"<inline:{len(loaded)}>")
                loaded.append(parsed)
        for path in paths:
            if path.suffix.casefold() == ".json":
                data, file_errors = load_json_file(path)
                errors.extend(file_errors)
                if data is None:
                    continue
                parsed = parse_envelope(data, str(path.resolve()))
                loaded.append(parsed)
                continue

            # Plain narrative files often accompany extracted PDFs/workbooks
            # without their own JSON envelope. Preserve them as text evidence
            # so every submission document can participate in whole-case and
            # specialist review. Keep source line ranges for auditable cites.
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                errors.append(f"Cannot read text submission file {path.name}: {exc}")
                continue
            lines = text.splitlines()
            chunks = []
            for start in range(0, len(lines), 20):
                block = "\n".join(lines[start:start + 20]).strip()
                if block:
                    chunks.append({
                        "line_start": start + 1,
                        "line_end": min(start + 20, len(lines)),
                        "text": block,
                    })
            if not chunks and text.strip():
                chunks.append({"line_start": 1, "line_end": max(1, len(lines)),
                               "text": text.strip()})
            envelope = InputEnvelope(
                submission_id=request.submission_id,
                document={
                    "document_id": f"TEXT-{path.stem}",
                    "document_type": "unstructured_document",
                    "document_name": path.name,
                },
                data={"text_chunks": chunks},
                provenance={"extraction_source": "direct_text_file",
                            "source_file": path.name},
            )
            loaded.append(LoadedDocument(envelope, str(path.resolve()), len(loaded)))
        return loaded, errors

    @staticmethod
    def _input_hash(request: ReviewRequest, config_versions: Dict[str, str]) -> str:
        h = hashlib.sha256()
        h.update(json.dumps(config_versions, sort_keys=True).encode())
        if request.input_dir:
            for p in submission_input_paths(Path(request.input_dir)):
                h.update(p.name.encode())
                h.update(p.read_bytes())
        for doc in request.documents:
            if doc.path and Path(doc.path).exists():
                h.update(Path(doc.path).read_bytes())
            if doc.content is not None:
                h.update(json.dumps(doc.content, sort_keys=True, default=str).encode())
        return h.hexdigest()

    @staticmethod
    def _is_partial(result: ReviewResult) -> bool:
        return any(s.status == StageStatus.INCOMPLETE for s in result.stages) or bool(
            result.errors and result.status != CaseStatus.ERROR_INCOMPLETE)
