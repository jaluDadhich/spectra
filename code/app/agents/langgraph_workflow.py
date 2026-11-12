"""LangGraph review graph with per-line agents, memory, and trace events."""
from __future__ import annotations

import json
import logging
import operator
import time
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from app.config import Settings
from app.external_sources.research_tool import ExternalResearchTool
from app.llm.tasks import SemanticReviewer
from app.schemas.canonical import CanonicalCase
from app.schemas.findings import Finding
from app.storage.repository import CaseRepository

logger = logging.getLogger("spectra.agents")


class ReviewState(TypedDict, total=False):
    findings: Annotated[List[Dict[str, Any]], operator.add]
    trace: Annotated[List[Dict[str, Any]], operator.add]
    external_evidence_ids: List[str]
    gpt_failed: Annotated[bool, operator.or_]
    failure_reasons: Annotated[List[str], operator.add]
    scope_findings: List[Dict[str, Any]]


_ALLOWED_ISSUE_FAMILIES = {
    "intake_agent": [f"U{n:02d}" for n in range(1, 15)],
    "wc_agent": ["U03 payroll", "U04 experience modifier", "U05 state/classification",
                 "U06 employee counts", "U07 class rates", "U08 loss history", "U13 external context"],
    "gl_agent": ["U09 sales/area basis", "U13 operations/external context"],
    "auto_agent": ["U10 active fleet", "U11 garaging", "U12 driver qualification", "U13 external context"],
    "property_agent": ["U01 locations/property values", "U13 external context"],
    "feedback_agent": [f"U{n:02d}" for n in range(1, 15)],
}


def run_agent_graph(case: CanonicalCase, reviewer: SemanticReviewer,
                    settings: Settings, repository: Optional[CaseRepository],
                    run_id: str, account_memory_key: Optional[str],
                    seed_findings: Optional[List[Finding]] = None) -> Dict[str, Any]:
    """Run a durable LangGraph workflow; all state is serializable for checkpoints."""
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(ReviewState)

    def emod_calculation_node(state: ReviewState) -> Dict[str, Any]:
        seeds = seed_findings or []
        emod = [f for f in seeds if f.comparison and f.comparison.calculation]
        calculation = emod[0].comparison.calculation if emod else None
        return {"trace": [
            {
                "node": "deterministic_candidate_generation",
                "status": "candidate_ready",
                "model_tier": "deterministic_candidate_rules",
                "candidate_count": len(seeds),
                "candidate_types": sorted({f.finding_type for f in seeds}),
                "finding_ids": [f.finding_id for f in seeds],
                "reason": "Candidates are sent to the GPT scope judge; they are not final findings",
                "calls": 0,
            },
            {
                "node": "experience_mod_calculation",
                "status": "calculated" if emod else "no_candidate",
                "model_tier": "deterministic_calculation",
                "finding_ids": [f.finding_id for f in emod],
                "formula": calculation.formula if calculation else None,
                "calculated_value": calculation.result if calculation else None,
                "reason": ("Date-qualified experience-mod comparison seeded for GPT adjudication"
                           if emod else "No current experience-mod disagreement to calculate"),
                "calls": 0,
            },
        ]}

    graph.add_node("experience_mod_calculation", emod_calculation_node)

    def research_node(state: ReviewState) -> Dict[str, Any]:
        tool = ExternalResearchTool(settings, repository)
        try:
            out = tool.search(case)
            trace_event = {
                "node": "external_research_tool", "status": out.get("mode", "unknown"),
                "live_calls": out.get("live_calls", 0),
                "cache_hits": out.get("cache_hits", 0),
                "evidence_count": len(out.get("evidence_ids", [])),
                "reason": out.get("reason", ""),
                "errors": out.get("errors", [])[:3],
            }
            logger.info("event=external_research_completed run_id=%s mode=%s local_records=%s live_calls=%d cache_hits=%d evidence=%d errors=%d",
                        run_id, trace_event["status"],
                        bool(out.get("reason") and "local" in out.get("reason", "").lower()),
                        trace_event["live_calls"], trace_event["cache_hits"],
                        trace_event["evidence_count"], len(trace_event["errors"]))
            return {"external_evidence_ids": out.get("evidence_ids", []),
                    "trace": [trace_event]}
        except Exception as exc:
            # Research provider failures are traceable but do not prevent review
            # of the submission evidence already in hand.
            logger.exception("event=external_research_failed run_id=%s", run_id)
            return {"external_evidence_ids": [], "trace": [{
                "node": "external_research_tool", "status": "error",
                "live_calls": 0, "error": f"{type(exc).__name__}: {exc}"}]}

    graph.add_node("external_research", research_node)

    doc_types_by_id = {d.document_id: d.document_type for d in case.documents}
    doc_names_by_id = {d.document_id: d.document_name or d.document_id
                       for d in case.documents}
    all_types = set(doc_types_by_id.values())

    def build_agent_node(agent_name: str, relevant_types: Optional[set[str]],
                         question: str, fast: bool = False):
        def node(state: ReviewState) -> Dict[str, Any]:
            if state.get("gpt_failed"):
                return {"trace": [{"node": agent_name, "status": "skipped",
                                    "reason": "previous GPT agent failed"}]}
            if relevant_types is None:
                evidence_ids = list(case.evidence_store.keys())
            else:
                # Unstructured OCR/text packets can contain ACORD sections
                # for several LOBs; include them in every specialist audit.
                specific_types = set(relevant_types) - {"unstructured_document"}
                specific_docs = sorted(
                    doc_id for doc_id, dtype in doc_types_by_id.items()
                    if dtype in specific_types)
                unstructured_docs = sorted(
                    doc_id for doc_id, dtype in doc_types_by_id.items()
                    if dtype == "unstructured_document" and
                    "unstructured_document" in relevant_types)
                relevant_docs = set(specific_docs + unstructured_docs)
                if "external_records" in relevant_types:
                    relevant_docs.update(
                        item.document_id for item in case.evidence_store.values()
                        if item.document_id.startswith("EXT-WEB-"))
                # Prioritize typed line documents, then shared OCR packets.
                evidence_ids = [eid for doc_id in specific_docs + unstructured_docs
                                for eid, item in case.evidence_store.items()
                                if item.document_id == doc_id]
                selected_ids = set(evidence_ids)
                evidence_ids.extend(
                    eid for eid, item in case.evidence_store.items()
                    if item.document_id in relevant_docs and eid not in selected_ids)
            if not evidence_ids:
                return {"trace": [{"node": agent_name, "status": "skipped",
                                    "reason": "no relevant document evidence",
                                    "document_types": sorted(all_types)}]}

            agent_started = time.monotonic()
            logger.info("event=agent_started run_id=%s agent=%s evidence=%d documents=%d",
                        run_id, agent_name, len(evidence_ids),
                        len({case.evidence_store[eid].document_id for eid in evidence_ids}))

            memory: List[Dict[str, Any]] = []
            if repository and account_memory_key:
                memory = repository.get_agent_memory(account_memory_key, agent_name)
            calls_before = reviewer.tracker.usage.calls
            intake: List[Dict[str, Any]] = []
            if agent_name in {"wc_agent", "gl_agent", "auto_agent", "property_agent"}:
                relevant_doc_ids = set(specific_docs + unstructured_docs)
                if "external_records" in relevant_types:
                    relevant_doc_ids.update(
                        doc_id for doc_id, dtype in doc_types_by_id.items()
                        if dtype == "external_records")
                intake = [{"title": f.get("title"), "evidence": [
                    e.get("evidence_id") for e in f.get("evidence", [])]}
                    for f in state.get("findings", [])
                    if any(e.get("document_id") in relevant_doc_ids
                           for e in f.get("evidence", []))]

            allowed_families = _ALLOWED_ISSUE_FAMILIES.get(
                agent_name, [f"U{n:02d}" for n in range(1, 15)])
            schema_name = "document_triage" if fast else "spectra_candidates"

            def review_batch(batch_ids: List[str], depth: int = 0
                             ) -> tuple[List[Finding], List[str], bool]:
                batch_question = question
                batch_id_set = set(batch_ids)
                batch_intake = [item for item in intake
                                if batch_id_set.intersection(item.get("evidence", []))]
                if batch_intake:
                    batch_question += " Intake-agent observations to independently verify: " + \
                        json.dumps(batch_intake, default=str)

                batch_findings, batch_notes = reviewer.review(
                    case=case,
                    task_id=agent_name,
                    question=batch_question,
                    allowed_families=allowed_families,
                    evidence_ids=batch_ids,
                    schema_name=schema_name,
                    reviewer_memory=memory,
                )
                batch_succeeded = reviewer.last_call_succeeded
                token_budget_hit = (not batch_succeeded and any(
                    "Estimated prompt exceeds the per-request token budget" in note
                    for note in batch_notes))
                if token_budget_hit and len(batch_ids) > 1:
                    midpoint = len(batch_ids) // 2
                    logger.info(
                        "event=agent_evidence_batch_split run_id=%s agent=%s reason=token_budget evidence=%d depth=%d",
                        run_id, agent_name, len(batch_ids), depth)
                    left_findings, left_notes, left_ok = review_batch(
                        batch_ids[:midpoint], depth + 1)
                    right_findings, right_notes, right_ok = review_batch(
                        batch_ids[midpoint:], depth + 1)
                    return (left_findings + right_findings,
                            left_notes + right_notes, left_ok and right_ok)
                return batch_findings, batch_notes, batch_succeeded

            findings, notes, success = review_batch(evidence_ids)
            event = {
                "node": agent_name,
                "status": "completed" if success else "failed",
                "model_tier": "fast" if fast else "reasoning",
                "evidence_count": len(evidence_ids),
                "document_count": len({case.evidence_store[eid].document_id
                                        for eid in evidence_ids}),
                "document_names": sorted({
                    doc_names_by_id.get(case.evidence_store[eid].document_id,
                                        case.evidence_store[eid].document_id)
                    for eid in evidence_ids
                }),
                "memory_feedback_count": len(memory),
                "calls": reviewer.tracker.usage.calls - calls_before,
                "finding_ids": [f.finding_id for f in findings],
                "notes": notes[:5],
            }
            logger.info("event=agent_finished run_id=%s agent=%s status=%s findings=%d calls=%d elapsed_seconds=%.3f memory_items=%d",
                        run_id, agent_name, event["status"], len(findings),
                        event["calls"], time.monotonic() - agent_started,
                        len(memory))
            update: Dict[str, Any] = {"trace": [event]}
            if findings:
                update["findings"] = [f.model_dump(mode="json") for f in findings]
            if not success:
                update["gpt_failed"] = True
                update["failure_reasons"] = [
                    "; ".join(notes) or f"{agent_name} failed"]
            return update
        return node

    graph.add_node("intake_agent", build_agent_node(
        "intake_agent", None,
        "Classify the submission and document types, map unfamiliar data fields, "
        "identify missing or contradictory information, and flag broad data-quality "
        "issues. Do not make ungrounded assumptions.", fast=True))
    graph.add_node("wc_agent", build_agent_node(
        "wc_agent", {"acord_130", "wc_rating_workbook", "loss_run", "external_records", "unstructured_document"},
        "Review workers compensation exposures: class codes, states, payroll, employee "
        "counts, experience modification factors, policy periods, and related quote inputs. "
        "Reconcile only aligned entities and periods.", fast=True))
    graph.add_node("gl_agent", build_agent_node(
        "gl_agent", {"acord_126", "quote", "broker_email", "ops_supplement", "external_records", "unstructured_document"},
        "Review general liability and package exposures: operations, sales, area, employee "
        "counts, limits, exclusions, and quote inputs. Compare scope and periods carefully.", fast=True))
    graph.add_node("auto_agent", build_agent_node(
        "auto_agent", {"fleet_schedule", "driver_roster", "external_records", "unstructured_document"},
        "Review commercial auto exposures: fleet counts, VINs, vehicle status, GVWR, garaging, "
        "driver roster/qualification evidence, and quote inputs. Missing records are gaps, not "
        "proof of adverse facts.", fast=True))
    graph.add_node("property_agent", build_agent_node(
        "property_agent", {"acord_140", "sov", "external_records", "unstructured_document"},
        "Review property locations, building and contents values, TIV, occupancy, construction "
        "or protection details when present, and quoted property terms. Check location identity.", fast=True))

    def feedback_node(state: ReviewState) -> Dict[str, Any]:
        if state.get("gpt_failed"):
            return {"trace": [{"node": "feedback_agent", "status": "skipped",
                                "reason": "previous GPT agent failed"}]}
        # The final scope auditor independently checks every deterministic
        # candidate. The feedback loop is for self-critique of model-generated
        # candidates only; replaying all structured rule findings here causes
        # large citation batches and duplicates the scope-audit work.
        prior = [item for item in state.get("findings", [])
                 if str(item.get("rule_id", "")).startswith("GPT-")]
        if not prior:
            return {"trace": [{
                "node": "feedback_agent", "status": "skipped",
                "reason": "no model-generated candidate findings to critique",
                "candidate_count": 0, "calls": 0,
            }]}
        # Candidate summaries and all-document evidence together can exceed
        # the hard request budget. Critique bounded candidate batches using
        # only their cited evidence; split again if the exact prompt estimator
        # says a batch still does not fit. Context-rejected attempts do not make
        # an API call or consume the per-case call budget.
        started = time.monotonic()
        calls_before = reviewer.tracker.usage.calls
        memory: List[Dict[str, Any]] = []
        if repository and account_memory_key:
            memory = repository.get_agent_memory(account_memory_key, "feedback_agent")
        memory_context = [{
            key: item.get(key) for key in ("taxonomy_id", "finding_type", "action", "rationale")
            if item.get(key) is not None
        } for item in memory]
        findings_out: List[Finding] = []
        notes: List[str] = []
        evidence_seen: set[str] = set()
        documents_seen: set[str] = set()
        completed_batches = 0
        failed = False

        initial_batches = [prior[i:i + 30] for i in range(0, len(prior), 30)] or [[]]

        def review_feedback_batch(batch: List[Dict[str, Any]], depth: int = 0) -> None:
            nonlocal completed_batches, failed
            batch_evidence_ids = list(dict.fromkeys(
                str(ref.get("evidence_id"))
                for candidate in batch
                for ref in candidate.get("evidence", [])
                if ref.get("evidence_id") in case.evidence_store
            ))
            if not batch and not batch_evidence_ids:
                batch_evidence_ids = list(case.evidence_store.keys())

            # Confirm citations all fit in this bundle before asking the model
            # to adjudicate the accompanying candidate summaries.
            bundle = reviewer._bundle(case, batch_evidence_ids)
            bundled_ids = set()
            for line in bundle.splitlines():
                try:
                    value = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, dict) and value.get("evidence_id"):
                    bundled_ids.add(value["evidence_id"])
            missing_citations = set(batch_evidence_ids) - bundled_ids

            summary = [{
                "finding_id": item.get("finding_id"),
                "title": str(item.get("title") or "")[:140],
                "type": item.get("finding_type"),
                "rule_id": item.get("rule_id"),
                "evidence_ids": [ref.get("evidence_id") for ref in item.get("evidence", [])
                                 if ref.get("evidence_id") in bundled_ids],
            } for item in batch]
            question = (
                "Independent critic pass. Re-check the supplied evidence against this batch of "
                "prior candidates. Identify only additional, distinct, in-scope, evidence-supported "
                "issues; do not repeat the listed candidates. Candidate batch: "
                + json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str)
            )

            if missing_citations and len(batch) > 1:
                split = len(batch) // 2
                logger.info("event=feedback_batch_split run_id=%s reason=citation_bundle_limit "
                            "candidates=%d missing_evidence=%d depth=%d",
                            run_id, len(batch), len(missing_citations), depth)
                review_feedback_batch(batch[:split], depth + 1)
                review_feedback_batch(batch[split:], depth + 1)
                return

            batch_findings, batch_notes = reviewer.review(
                case=case, task_id="feedback_agent", question=question,
                allowed_families=_ALLOWED_ISSUE_FAMILIES["feedback_agent"],
                evidence_ids=batch_evidence_ids,
                schema_name="document_triage", reviewer_memory=memory,
            )
            if not reviewer.last_call_succeeded:
                reason = "; ".join(batch_notes)
                if ("Estimated prompt exceeds the per-request token budget" in reason
                        and len(batch) > 1):
                    split = len(batch) // 2
                    logger.info("event=feedback_batch_split run_id=%s reason=token_budget "
                                "candidates=%d evidence=%d depth=%d",
                                run_id, len(batch), len(batch_evidence_ids), depth)
                    review_feedback_batch(batch[:split], depth + 1)
                    review_feedback_batch(batch[split:], depth + 1)
                    return
                notes.extend(batch_notes or ["Feedback candidate batch failed"])
                failed = True
                return

            completed_batches += 1
            findings_out.extend(batch_findings)
            evidence_seen.update(batch_evidence_ids)
            documents_seen.update(
                case.evidence_store[eid].document_id
                for eid in batch_evidence_ids if eid in case.evidence_store)
            notes.extend(batch_notes)

        logger.info("event=agent_started run_id=%s agent=feedback_agent candidates=%d batches=%d",
                    run_id, len(prior), len(initial_batches))
        for batch in initial_batches:
            review_feedback_batch(batch)
            if failed:
                break

        event = {
            "node": "feedback_agent",
            "status": "failed" if failed else "completed",
            "model_tier": "fast",
            "evidence_count": len(evidence_seen),
            "document_count": len(documents_seen),
            "document_names": sorted(doc_names_by_id.get(doc_id, doc_id)
                                      for doc_id in documents_seen),
            "memory_feedback_count": len(memory),
            "calls": reviewer.tracker.usage.calls - calls_before,
            "feedback_batches": completed_batches,
            "candidate_count": len(prior),
            "finding_ids": [finding.finding_id for finding in findings_out],
            "notes": notes[:5],
        }
        logger.info("event=agent_finished run_id=%s agent=feedback_agent status=%s findings=%d "
                    "calls=%d feedback_batches=%d elapsed_seconds=%.3f memory_items=%d",
                    run_id, event["status"], len(findings_out), event["calls"],
                    completed_batches, time.monotonic() - started, len(memory))
        update: Dict[str, Any] = {"trace": [event]}
        if findings_out:
            update["findings"] = [finding.model_dump(mode="json") for finding in findings_out]
        if failed:
            update["gpt_failed"] = True
            update["failure_reasons"] = ["; ".join(notes) or "Feedback agent failed"]
        return update

    graph.add_node("feedback_agent", feedback_node)

    def scope_audit_node(state: ReviewState) -> Dict[str, Any]:
        if state.get("gpt_failed"):
            return {"trace": [{"node": "scope_auditor", "status": "skipped",
                                "reason": "previous GPT agent failed"}]}
        try:
            from app.catalog.issue_register import load_register
            catalog = load_register(settings.spectra_ruleset_dir).model_dump()
            all_candidates = [Finding.model_validate(item) for item in state.get("findings", [])]
            # A deterministic seed can occasionally have no source citations.
            # Such an item cannot be adjudicated against evidence, but should
            # not abort judgment of every other candidate.
            uncited_candidates = [finding for finding in all_candidates if not finding.evidence]
            candidates = [finding for finding in all_candidates if finding.evidence]
            if uncited_candidates:
                logger.warning(
                    "event=scope_candidates_excluded_uncited run_id=%s count=%d finding_ids=%s",
                    run_id, len(uncited_candidates),
                    [finding.finding_id for finding in uncited_candidates])
            candidate_evidence = {
                ref.evidence_id for finding in candidates for ref in finding.evidence
            }
            candidate_documents = {
                case.evidence_store[eid].document_id for eid in candidate_evidence
                if eid in case.evidence_store
            }
            calls_before = reviewer.tracker.usage.calls
            logger.info("event=scope_judge_started run_id=%s candidates=%d evidence=%d",
                        run_id, len(candidates), len(candidate_evidence))
            if candidates:
                scoped, notes = reviewer.adjudicate_scope(case, candidates, catalog)
                success = reviewer.last_call_succeeded
            else:
                scoped, notes = [], []
                reviewer.last_call_succeeded = True
                success = True
            severity_counts: Dict[str, int] = {}
            nature_counts: Dict[str, int] = {}
            if success:
                for finding in scoped:
                    severity_counts[finding.severity.value] = severity_counts.get(finding.severity.value, 0) + 1
                    nature = finding.issue_nature.value if finding.issue_nature else "OTHER"
                    nature_counts[nature] = nature_counts.get(nature, 0) + 1
            event = {
                "node": "scope_auditor",
                "status": "completed" if success else "failed",
                "model_tier": "reasoning",
                "judge_mode": "llm_as_judge",
                "input_findings": len(all_candidates),
                "accepted_findings": len(scoped) if success else 0,
                "dropped_findings": max(0, len(all_candidates) - len(scoped)) if success else 0,
                "excluded_uncited_findings": len(uncited_candidates),
                "severity_distribution": severity_counts,
                "issue_nature_distribution": nature_counts,
                "evidence_count": len(candidate_evidence),
                "document_count": len(candidate_documents),
                "document_names": sorted(doc_names_by_id.get(doc_id, doc_id)
                                          for doc_id in candidate_documents),
                "calls": reviewer.tracker.usage.calls - calls_before,
                "finding_ids": [f.finding_id for f in scoped] if success else [],
                "reason": (f"Scoped {len(candidates)} evidence-cited candidates against catalog; "
                           f"retained {len(scoped)} in-scope finding(s); excluded "
                           f"{len(uncited_candidates)} uncited candidate(s)"
                           if success else "Scope adjudication failed; deterministic fallback required"),
                "notes": (notes[:3] + ([f"Excluded {len(uncited_candidates)} candidate(s) without evidence citations"]
                                      if uncited_candidates else []))[:3],
                "catalog_id": catalog.get("catalog_id"),
                "catalog_version": catalog.get("version"),
            }
            candidate_ids = {finding.finding_id for finding in all_candidates}
            kept_candidate_count = sum(1 for finding in scoped
                                       if finding.finding_id in candidate_ids)
            dropped_candidate_count = max(0, len(all_candidates) - kept_candidate_count)
            added_count = sum(1 for finding in scoped
                              if finding.finding_id not in candidate_ids)
            event["accepted_findings"] = kept_candidate_count + added_count
            event["dropped_findings"] = dropped_candidate_count
            event["added_findings"] = added_count
            logger.info("event=scope_judge_finished run_id=%s status=%s input=%d judged=%d accepted=%d dropped=%d uncited=%d added=%d calls=%d",
                        run_id, event["status"], len(all_candidates), len(candidates),
                        kept_candidate_count + added_count if success else 0,
                        dropped_candidate_count if success else 0,
                        len(uncited_candidates),
                        added_count if success else 0,
                        reviewer.tracker.usage.calls - calls_before)
            update: Dict[str, Any] = {"trace": [event]}
            if success:
                update["scope_findings"] = [f.model_dump(mode="json") for f in scoped]
            else:
                update["gpt_failed"] = True
                update["failure_reasons"] = ["; ".join(notes) or "Scope audit failed"]
            return update
        except Exception as exc:
            logger.exception("event=scope_judge_crashed run_id=%s", run_id)
            return {"gpt_failed": True,
                    "failure_reasons": [f"Issue scope audit failed: {type(exc).__name__}: {exc}"],
                    "trace": [{"node": "scope_auditor", "status": "failed",
                               "error": f"{type(exc).__name__}: {exc}"}]}

    graph.add_node("scope_auditor", scope_audit_node)
    graph.add_edge(START, "experience_mod_calculation")
    graph.add_edge("experience_mod_calculation", "external_research")
    graph.add_edge("external_research", "intake_agent")
    # Specialist agents are distinct graph nodes, run in a controlled order
    # so the shared per-case budget and evidence context stay deterministic.
    graph.add_edge("intake_agent", "wc_agent")
    graph.add_edge("wc_agent", "gl_agent")
    graph.add_edge("gl_agent", "auto_agent")
    graph.add_edge("auto_agent", "property_agent")
    graph.add_edge("property_agent", "feedback_agent")
    graph.add_edge("feedback_agent", "scope_auditor")
    graph.add_edge("scope_auditor", END)

    initial: ReviewState = {"findings": [f.model_dump(mode="json")
                                         for f in (seed_findings or [])],
                            "trace": [], "external_evidence_ids": [],
                            "gpt_failed": False, "failure_reasons": []}
    config = {"configurable": {"thread_id": run_id}}
    if repository:
        from langgraph.checkpoint.sqlite import SqliteSaver
        with SqliteSaver.from_conn_string(str(repository.db_path)) as saver:
            compiled = graph.compile(checkpointer=saver)
            output = compiled.invoke(initial, config=config)
    else:
        from langgraph.checkpoint.memory import MemorySaver
        output = graph.compile(checkpointer=MemorySaver()).invoke(initial, config=config)

    for event in output.get("trace", []):
        if repository:
            repository.audit("agent_trace", run_id=run_id,
                             actor=str(event.get("node", "agent")),
                             detail=json.dumps(event, default=str))
    call_count = sum(int(event.get("calls", 0)) for event in output.get("trace", []))
    failed = bool(output.get("gpt_failed")) or call_count == 0
    failure_reasons = output.get("failure_reasons", [])
    if call_count == 0 and not failure_reasons:
        failure_reasons = ["No GPT agent received usable evidence; deterministic fallback required"]
    return {
        "findings": [Finding.model_validate(item) for item in output.get("scope_findings", [])],
        "trace": output.get("trace", []),
        "gpt_failed": failed,
        "failure_reason": "; ".join(failure_reasons),
        "external_evidence_ids": output.get("external_evidence_ids", []),
    }
