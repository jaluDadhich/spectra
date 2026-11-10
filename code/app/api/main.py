"""FastAPI surface (spec section 12).

Auth boundary hook: when SPECTRA_API_TOKEN is set, all /api routes require
`Authorization: Bearer <token>`. When unset the API runs in local
single-user mode — configure a token or an auth gateway before multi-user
production deployment (see docs/production-readiness.md).
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.agents import Orchestrator
from app.adapters.loader import submission_input_paths
from app.config import Settings, get_settings
from app.evidence import build_export_json, render_csv, render_markdown_report
from app.evidence.source_viewer import (
    find_matching_pdf_page, find_pdf_highlights, resolve_source_file,
    source_page_range, workbook_preview,
)
from app.findings import compute_case_status, rank_findings
from app.logging_config import configure_logging
from app.normalization import normalize_address
from app.progress import ProgressTracker, TOTAL_STAGES
from app.schemas.findings import DispositionAction, FindingStatus, ReviewerDisposition
from app.schemas.review import CaseStatus, ReviewRequest, ReviewResult
from app.storage import CaseRepository

logger = logging.getLogger("spectra.api")

_bearer = HTTPBearer(auto_error=False)

_ACTION_TO_STATUS = {
    DispositionAction.CONFIRM: FindingStatus.CONFIRMED,
    DispositionAction.REJECT_FALSE_POSITIVE: FindingStatus.REJECTED_FALSE_POSITIVE,
    DispositionAction.RESOLVE: FindingStatus.RESOLVED,
    DispositionAction.REQUEST_CLARIFICATION: FindingStatus.NEEDS_REVIEW,
    DispositionAction.REFER_SPECIALIST: FindingStatus.NEEDS_REVIEW,
    DispositionAction.WAIVE: FindingStatus.WAIVED,
    DispositionAction.MARK_NOT_APPLICABLE: FindingStatus.NOT_APPLICABLE,
}


class DispositionRequest(BaseModel):
    action: DispositionAction
    reviewer_id: str = Field(min_length=1)
    rationale: str = Field(min_length=3)
    new_status: Optional[FindingStatus] = None  # only for OVERRIDE_STATUS


def create_app(settings: Optional[Settings] = None,
               repository: Optional[CaseRepository] = None) -> FastAPI:
    settings = settings or get_settings()
    log_path = configure_logging(settings)
    logger.info("event=api_starting log_file=%s llm_enabled=%s fast_model=%s reasoning_model=%s",
                log_path, settings.llm_available,
                settings.openai_fast_model or settings.openai_model,
                settings.openai_reasoning_model or settings.openai_model)
    repository = repository or CaseRepository(settings.sqlite_path)
    progress = ProgressTracker()
    workspace_root = Path(__file__).resolve().parents[3]
    app = FastAPI(title="Spectra", version="1.0.0",
                  description="Submission Pre-Bind Evidence Check and Truth Review Agent")
    static_dir = Path(__file__).resolve().parent.parent / "ui" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(
        directory=str(Path(__file__).resolve().parent.parent / "ui" / "templates"))

    async def auth(credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer)):
        token = settings.spectra_api_token
        if token is None:
            return "local-user"
        expected = token.get_secret_value() if hasattr(token, "get_secret_value") else str(token)
        if credentials is None or credentials.credentials != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return "api-user"

    # ----------------------------- health -----------------------------
    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        problems = []
        try:
            from app.rules import load_registry
            load_registry("default", settings.spectra_ruleset_dir)
        except Exception as exc:
            problems.append(f"ruleset: {exc}")
        try:
            from app.catalog.profiles import load_profile
            load_profile("commercial_package_default", settings.spectra_profile_dir)
        except Exception as exc:
            problems.append(f"profile: {exc}")
        status = 200 if not problems else 503
        # Never expose secrets; report only dependency health.
        return JSONResponse(
            {"ready": not problems, "problems": problems,
             "llm_enabled": settings.spectra_llm_enabled},
            status_code=status)

    # ----------------------------- reviews -----------------------------
    @app.post("/api/v1/reviews", response_model=None, status_code=201)
    def create_review(request: ReviewRequest, sync: bool = Query(default=True),
                      user: str = Depends(auth)):
        orchestrator = Orchestrator(settings=settings, repository=repository)
        # Local prototype executes synchronously; async execution can be
        # layered on via a task queue without changing the contract.
        result = orchestrator.run_review(request)
        repository.audit("api_review_created", run_id=result.run_id, actor=user,
                         detail=f"submission={request.submission_id}")
        return _result_payload(result, settings, workspace_root)

    @app.get("/api/v1/reviews")
    def list_reviews(submission_id: Optional[str] = None, user: str = Depends(auth)):
        return {"runs": repository.list_runs(submission_id)}

    @app.get("/api/v1/reviews/{run_id}")
    def get_review(run_id: str, user: str = Depends(auth)):
        result = repository.get_run(run_id)
        if result is None:
            raise HTTPException(404, "run not found")
        return _result_payload(result, settings, workspace_root)

    @app.get("/api/v1/reviews/{run_id}/findings")
    def list_findings(run_id: str, taxonomy: Optional[str] = None,
                      severity: Optional[str] = None, status: Optional[str] = None,
                      limit: int = Query(default=50, le=500), offset: int = 0,
                      user: str = Depends(auth)):
        result = repository.get_run(run_id)
        if result is None:
            raise HTTPException(404, "run not found")
        findings = result.findings
        if taxonomy:
            findings = [f for f in findings if f.taxonomy_id.startswith(taxonomy)]
        if severity:
            findings = [f for f in findings if f.severity.value == severity]
        if status:
            findings = [f for f in findings if f.status.value == status]
        total = len(findings)
        items = []
        for finding in findings[offset:offset + limit]:
            item = finding.model_dump(mode="json")
            _decorate_finding_sources(item, result, settings, workspace_root)
            items.append(item)
        return {"total": total, "offset": offset, "limit": limit, "findings": items}

    @app.get("/api/v1/reviews/{run_id}/findings/{finding_id}")
    def get_finding(run_id: str, finding_id: str, user: str = Depends(auth)):
        finding = repository.get_finding(run_id, finding_id)
        if finding is None:
            raise HTTPException(404, "finding not found")
        payload = finding.model_dump(mode="json")
        result = repository.get_run(run_id)
        if result:
            _decorate_finding_sources(payload, result, settings, workspace_root)
        return payload

    @app.get("/api/v1/reviews/{run_id}/evidence/{evidence_id}/source")
    def evidence_source(run_id: str, evidence_id: str, user: str = Depends(auth)):
        result = repository.get_run(run_id)
        evidence = _find_evidence(result, evidence_id) if result else None
        if result is None or evidence is None:
            raise HTTPException(404, "evidence citation not found")
        source = resolve_source_file(result, evidence, settings.accounts_dir, workspace_root)
        if source is None:
            raise HTTPException(404, "original PDF/XLSX source could not be resolved")
        location = evidence.source_location
        payload = {
            "file_name": source.name,
            "document_label": (evidence.document_name
                               if evidence.document_name and "<inline:" not in evidence.document_name
                               and Path(evidence.document_name.split(" :: ", 1)[0]).suffix.lower() != ".json"
                               else source.name),
            "kind": "pdf" if source.suffix.lower() == ".pdf" else "workbook",
            "page": None,
            "page_range": None,
            "sheet": location.sheet,
            "row": location.row,
            "line_start": location.line_start,
            "line_end": location.line_end,
            "cell": location.cell,
            "column": location.column,
            "raw_value": evidence.raw_value,
            "normalized_value": evidence.normalized_value,
            "download_url": (
                f"/api/v1/reviews/{quote(run_id, safe='')}/evidence/"
                f"{quote(evidence_id, safe='')}/download"),
        }
        if source.suffix.lower() == ".pdf":
            root = settings.accounts_dir.resolve()
            account_dir = (root / result.submission_id).resolve()
            try:
                account_dir.relative_to(root)
            except ValueError:
                account_dir = None
            page_range = source_page_range(source, evidence, account_dir)
            page = location.page or find_matching_pdf_page(source, evidence, page_range)
            if page_range:
                payload["page_range"] = list(page_range)
            if page is None and page_range:
                page = page_range[0]
                payload["page_match"] = "section_start"
            payload["page"] = page
            if page is not None:
                payload["preview_url"] = (
                    f"/api/v1/reviews/{quote(run_id, safe='')}/evidence/"
                    f"{quote(evidence_id, safe='')}/preview?page={page}")
        elif source.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
            try:
                payload["workbook_preview"] = workbook_preview(source, evidence)
            except (OSError, ValueError, KeyError) as exc:
                raise HTTPException(422, f"Workbook preview unavailable: {exc}") from exc
        else:
            try:
                source_lines = source.read_text(encoding="utf-8-sig").splitlines()
            except (OSError, UnicodeError) as exc:
                raise HTTPException(422, f"Text source preview unavailable: {exc}") from exc
            start = location.line_start or 1
            end = location.line_end or start
            preview_start = max(1, start - 2)
            preview_end = min(len(source_lines), end + 2)
            payload.update({
                "kind": "text",
                "line_start": start,
                "line_end": end,
                "preview_start_line": preview_start,
                "text_preview": "\n".join(source_lines[preview_start - 1:preview_end]),
            })
        return payload

    @app.get("/api/v1/reviews/{run_id}/evidence/{evidence_id}/preview")
    def evidence_preview(run_id: str, evidence_id: str,
                        page: Optional[int] = Query(default=None, ge=1),
                        user: str = Depends(auth)):
        result = repository.get_run(run_id)
        evidence = _find_evidence(result, evidence_id) if result else None
        if result is None or evidence is None:
            raise HTTPException(404, "evidence citation not found")
        source = resolve_source_file(result, evidence, settings.accounts_dir, workspace_root)
        if source is None or source.suffix.lower() != ".pdf":
            raise HTTPException(404, "PDF source could not be resolved")
        page_number = page or evidence.source_location.page
        if page_number is None:
            root = settings.accounts_dir.resolve()
            account_dir = (root / result.submission_id).resolve()
            try:
                account_dir.relative_to(root)
            except ValueError:
                account_dir = None
            page_range = source_page_range(source, evidence, account_dir)
            page_number = find_matching_pdf_page(source, evidence, page_range)
        try:
            import fitz
            with fitz.open(source) as pdf:
                if page_number is None or page_number > len(pdf):
                    raise HTTPException(404, "citation page could not be located in the PDF")
                page_obj = pdf.load_page(page_number - 1)
                matches = find_pdf_highlights(page_obj, evidence)
                for rect in matches:
                    page_obj.draw_rect(rect, color=(0.86, 0.58, 0.13),
                                       fill=(1.0, 0.82, 0.33), fill_opacity=0.3, width=1.3)
                pixmap = page_obj.get_pixmap(
                    matrix=fitz.Matrix(1.8, 1.8), alpha=False)
                image = pixmap.tobytes("png")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, f"Could not render cited PDF page: {exc}") from exc
        return Response(image, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=300"})

    @app.get("/api/v1/reviews/{run_id}/evidence/{evidence_id}/download")
    def evidence_download(run_id: str, evidence_id: str, user: str = Depends(auth)):
        result = repository.get_run(run_id)
        evidence = _find_evidence(result, evidence_id) if result else None
        if result is None or evidence is None:
            raise HTTPException(404, "evidence citation not found")
        source = resolve_source_file(result, evidence, settings.accounts_dir, workspace_root)
        if source is None:
            raise HTTPException(404, "original PDF/XLSX source could not be resolved")
        return FileResponse(source, filename=source.name)

    @app.post("/api/v1/reviews/{run_id}/findings/{finding_id}/disposition")
    def record_disposition(run_id: str, finding_id: str,
                           request: DispositionRequest, user: str = Depends(auth)):
        finding = repository.get_finding(run_id, finding_id)
        if finding is None:
            raise HTTPException(404, "finding not found")
        if request.action == DispositionAction.OVERRIDE_STATUS and request.new_status is None:
            raise HTTPException(422, "OVERRIDE_STATUS requires new_status")
        from datetime import datetime, timezone
        new_status = (request.new_status if request.action == DispositionAction.OVERRIDE_STATUS
                      else _ACTION_TO_STATUS[request.action])
        disposition = ReviewerDisposition(
            action=request.action, reviewer_id=request.reviewer_id,
            rationale=request.rationale,
            timestamp=datetime.now(timezone.utc).isoformat(),
            prior_status=finding.status.value, new_status=new_status.value,
            rule_versions={finding.rule_id: finding.rule_version})
        repository.record_disposition(run_id, finding_id, disposition)
        logger.info("event=finding_disposition run_id=%s finding_id=%s action=%s prior_status=%s new_status=%s",
                    run_id, finding_id, request.action.value,
                    finding.status.value, new_status.value)
        repository.audit("disposition", run_id=run_id, actor=request.reviewer_id,
                         detail=f"{finding_id}: {request.action.value}")
        # Recompute case status after disposition
        result = repository.get_run(run_id)
        if result:
            result.status = compute_case_status(result.findings, result.checks)
            repository.save_run(result, input_hash="")
        return {"recorded": True, "new_status": new_status.value}

    @app.post("/api/v1/reviews/{run_id}/rerun")
    def rerun(run_id: str, user: str = Depends(auth)):
        result = repository.get_run(run_id)
        if result is None:
            raise HTTPException(404, "run not found")
        logger.info("event=review_rerun_requested run_id=%s account=%s",
                    run_id, result.submission_id)
        raise HTTPException(
            501, "Re-run requires the original inputs; create a new review with "
                 "the updated JSON set. Prior run history is preserved.")

    @app.get("/api/v1/reviews/{run_id}/export")
    def export(run_id: str, format: str = Query(default="json", pattern="^(json|csv|markdown)$"),
               user: str = Depends(auth)):
        result = repository.get_run(run_id)
        if result is None:
            raise HTTPException(404, "run not found")
        if format == "csv":
            return PlainTextResponse(render_csv(result), media_type="text/csv")
        if format == "markdown":
            return PlainTextResponse(render_markdown_report(result),
                                     media_type="text/markdown")
        return build_export_json(result)

    # ----------------------------- accounts -----------------------------
    def _account_folder(account: str) -> Path:
        root = settings.accounts_dir.resolve()
        folder = (root / account).resolve()
        if folder.parent != root or not folder.is_dir():
            raise HTTPException(404, f"account not found: {account}")
        return folder

    @app.get("/api/v1/accounts")
    def list_accounts(user: str = Depends(auth)):
        root = settings.accounts_dir.resolve()
        accounts = []
        if root.is_dir():
            for folder in sorted(p for p in root.iterdir() if p.is_dir()):
                docs = submission_input_paths(folder)
                if not docs:
                    continue
                runs = repository.list_runs(submission_id=folder.name)
                latest = None
                if runs:
                    result = repository.get_run(runs[0]["run_id"])
                    if result is not None:
                        latest = {
                            "run_id": result.run_id,
                            "status": result.status.value,
                            "created_at": result.created_at,
                            "quality_score": (result.quality_score.model_dump(mode="json")
                                              if result.quality_score else None),
                        }
                accounts.append({
                    "account": folder.name,
                    "label": folder.name.replace("_", " "),
                    "document_count": len(docs),
                    "latest_run": latest,
                })
        return {"accounts_root": str(root), "accounts": accounts}

    @app.post("/api/v1/accounts/{account}/run", response_model=None, status_code=202)
    def start_account_run(account: str, background: BackgroundTasks,
                          line_of_business: str = Query(default="package"),
                          effective_date: Optional[str] = Query(default=None),
                          review_date: Optional[str] = Query(default=None),
                          deterministic_only: bool = Query(default=False),
                          user: str = Depends(auth)):
        folder = _account_folder(account)
        submission_docs = submission_input_paths(folder)
        if not submission_docs:
            raise HTTPException(422, f"Account {account} has no supported submission documents")
        run_id = f"RUN-{uuid.uuid4().hex[:12].upper()}"
        progress.start(run_id, account)
        logger.info("event=review_queued run_id=%s account=%s line_of_business=%s deterministic_only=%s documents=%d",
                    run_id, account, line_of_business, deterministic_only, len(submission_docs))

        def job() -> None:
            job_started = time.monotonic()
            logger.info("event=review_worker_started run_id=%s account=%s", run_id, account)
            orchestrator = Orchestrator(settings=settings, repository=repository)
            try:
                result = orchestrator.run_review(
                    ReviewRequest(
                        submission_id=account,
                        input_dir=str(folder),
                        line_of_business=line_of_business,
                        effective_date=effective_date,
                        review_date=review_date,
                        deterministic_only=deterministic_only),
                    progress_cb=progress.update, run_id=run_id)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Account run failed for %s", account)
                logger.error("event=review_worker_failed run_id=%s account=%s elapsed_seconds=%.3f",
                             run_id, account, time.monotonic() - job_started)
                progress.fail(run_id, f"{type(exc).__name__}: {exc}")
                return
            removed = repository.delete_runs_for_submission(
                account, keep_run_id=result.run_id)
            if removed:
                repository.audit("account_run_overridden", run_id=result.run_id,
                                 actor=user,
                                 detail=f"replaced {removed} prior run(s) for {account}")
            progress.complete(result.run_id, result.status.value)
            logger.info("event=review_worker_completed run_id=%s account=%s status=%s findings=%d elapsed_seconds=%.3f",
                        result.run_id, account, result.status.value,
                        len(result.findings), time.monotonic() - job_started)

        background.add_task(job)
        return {"run_id": run_id, "account": account,
                "progress_url": f"/api/v1/runs/{run_id}/progress",
                "total_stages": TOTAL_STAGES}

    @app.get("/api/v1/runs/{run_id}/progress")
    def run_progress(run_id: str, user: str = Depends(auth)):
        entry = progress.get(run_id)
        if entry is not None:
            return entry.model_dump(mode="json")
        result = repository.get_run(run_id)
        if result is None:
            raise HTTPException(404, "run not found")
        return {
            "run_id": run_id, "account": result.submission_id,
            "state": "COMPLETED", "percent": 100.0,
            "stage_index": TOTAL_STAGES - 1, "stage_name": "findings_synthesis",
            "message": f"Review finished with status {result.status.value}",
            "updated_at": result.completed_at or result.created_at,
        }

    # ----------------------------- UI -----------------------------
    @app.get("/", response_class=HTMLResponse)
    def ui_index(request: Request):
        return templates.TemplateResponse(request, "index.html", {
            "runs": repository.list_runs()})

    @app.get("/ui/runs/{run_id}", response_class=HTMLResponse)
    def ui_run(request: Request, run_id: str):
        result = repository.get_run(run_id)
        if result is None:
            raise HTTPException(404, "run not found")
        from urllib.parse import quote
        return RedirectResponse(f"/#run/{quote(run_id, safe='')}", status_code=307)

    @app.get("/ui/runs/{run_id}/findings/{finding_id}", response_class=HTMLResponse)
    def ui_finding(request: Request, run_id: str, finding_id: str):
        finding = repository.get_finding(run_id, finding_id)
        if finding is None:
            raise HTTPException(404, "finding not found")
        from urllib.parse import quote
        return RedirectResponse(
            f"/#run/{quote(run_id, safe='')}/finding/{quote(finding_id, safe='')}",
            status_code=307)

    @app.exception_handler(Exception)
    async def safe_errors(request: Request, exc: Exception):
        logger.exception("Unhandled API error")
        return JSONResponse({"detail": "Internal error"}, status_code=500)

    return app


def _find_evidence(result: Optional[ReviewResult], evidence_id: str):
    if result is None:
        return None
    for finding in result.findings:
        for evidence in finding.evidence:
            if evidence.evidence_id == evidence_id:
                return evidence
    return None


def _decorate_finding_sources(payload: dict, result: ReviewResult, settings: Settings,
                              workspace_root: Path,
                              source_cache: Optional[dict] = None) -> None:
    cache = source_cache if source_cache is not None else {}
    labels = {}
    for item in payload.get("evidence", []):
        evidence = _find_evidence(result, item.get("evidence_id", ""))
        if evidence is None:
            continue
        cache_key = evidence.source_file or "|".join((evidence.document_id,
            evidence.document_name or "", evidence.source_location.sheet or ""))
        if cache_key not in cache:
            cache[cache_key] = resolve_source_file(
                result, evidence, settings.accounts_dir, workspace_root)
        source = cache[cache_key]
        if source is None:
            continue
        section = (evidence.document_name.split(" :: ", 1)[1]
                   if evidence.document_name and " :: " in evidence.document_name else None)
        display_name = source.name + (f" :: {section}" if section else "")
        item["source_filename"] = display_name
        labels[evidence.document_id] = display_name
        if (not item.get("document_name")
                or item["document_name"].startswith("<inline:")
                or Path(item["document_name"].split(" :: ", 1)[0]).suffix.lower() == ".json"):
            item["document_name"] = display_name
    for key in ("title", "possible_consequence", "explanation"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        for document_id, label in sorted(labels.items(), key=lambda item: -len(item[0])):
            value = value.replace(document_id, label)
        value = re.sub(r"GEN-<inline:\d+>", "the other submission document", value)
        value = re.sub(r"<inline:\d+>", "submission document", value)
        payload[key] = value
    comparison = payload.get("comparison") or {}
    if not comparison.get("sources"):
        values = comparison.get("values_compared") or []
        groups = {}
        for item in payload.get("evidence", []):
            groups.setdefault(item.get("document_id", ""), []).append(item)
        finding_type = payload.get("finding_type")
        taxonomy = payload.get("taxonomy_id", "")
        inferred = []
        if finding_type == "LOCATION_SET_CONFLICT":
            for document_id, items in groups.items():
                label = labels.get(document_id) or items[0].get("document_name") or "Source document"
                inferred.append({
                    "document_id": document_id, "document_name": label,
                    "label": "Premises found", "value": "Present", "status": "PRESENT",
                    "evidence_ids": [item.get("evidence_id") for item in items],
                })
            missing_match = re.search(r"missing from (.+)$", payload.get("title", ""))
            if missing_match:
                missing_id = missing_match.group(1).strip()
                inferred.append({
                    "document_id": missing_id,
                    "document_name": labels.get(missing_id, "Other submission document"),
                    "label": "No matching premises row", "value": "Not listed",
                    "status": "NOT_LISTED", "evidence_ids": [],
                })
        elif (len(values) == len(groups) and values
              and (finding_type == "LOCATION_COUNT_CONFLICT"
                   or taxonomy == "P3-WC-PAYROLL")):
            for index, (document_id, items) in enumerate(groups.items()):
                label = labels.get(document_id) or items[0].get("document_name") or document_id
                inferred.append({
                    "document_id": document_id, "document_name": label, "label": "Compared total",
                    "value": values[index], "status": "COMPARED",
                    "evidence_ids": [item.get("evidence_id") for item in items],
                })
        if inferred:
            comparison["sources"] = inferred
    # Legacy stored findings may have been built when a repeated location
    # number alone was treated as proof that two rows describe the same site.
    # Repair the response presentation from its already-cited address values;
    # the saved review remains immutable and new runs use the corrected rule.
    if payload.get("finding_type") == "LOCATION_SET_CONFLICT":
        evidence_by_id = {item.get("evidence_id"): item
                          for item in payload.get("evidence", [])}
        addressed_sources = []
        for source in comparison.get("sources", []):
            address_items = []
            for evidence_id in source.get("evidence_ids", []):
                item = evidence_by_id.get(evidence_id, {})
                location = item.get("source_location") or {}
                path = str(location.get("json_path") or "")
                field_path = str(item.get("field_path") or "")
                path_leaf = path.rsplit(".", 1)[-1].split("[", 1)[0].casefold()
                field_leaf = field_path.rsplit(".", 1)[-1].casefold()
                if (path_leaf.endswith("address") or field_leaf.endswith("address")):
                    if item.get("raw_value") not in (None, ""):
                        address_items.append(item)
            addresses = list(dict.fromkeys(str(item["raw_value"]) for item in address_items))
            if addresses:
                source["value"] = "; ".join(addresses)
                source["status"] = "CONFLICTING"
                source["label"] = "Conflicting scheduled address"
                addressed_sources.append((source, addresses, address_items))
        normalized = {normalize_address(address) or address.casefold().strip()
                      for _, addresses, _ in addressed_sources for address in addresses}
        if len(addressed_sources) > 1 and len(normalized) > 1:
            comparison["sources"] = [
                source for source in comparison.get("sources", [])
                if source.get("status") != "NOT_LISTED"
            ]
            location_id = (payload.get("deduplication_key", "").split(":")[-1]
                           or "unknown")
            payload["title"] = (
                f"Location ID {location_id} has conflicting addresses across documents")
            details = [
                f"{source.get('document_name') or source.get('label')}: {', '.join(addresses)}"
                for source, addresses, _ in addressed_sources
            ]
            payload["possible_consequence"] = (
                f"Location ID {location_id} is recorded with different addresses: "
                + "; ".join(details))
    formula = comparison.get("delta_formula")
    if isinstance(formula, str):
        for document_id, label in sorted(labels.items(), key=lambda item: -len(item[0])):
            formula = formula.replace(document_id, label)
        formula = re.sub(r"GEN-<inline:\d+>", "submission document", formula)
        comparison["delta_formula"] = formula
    for source_info in comparison.get("sources", []):
        document_id = source_info.get("document_id")
        if document_id in labels:
            source_info["document_name"] = labels[document_id]
            source_info["label"] = labels[document_id]


def _result_payload(result: ReviewResult, settings: Optional[Settings] = None,
                    workspace_root: Optional[Path] = None) -> dict:
    payload = result.model_dump(mode="json")
    if settings is not None and workspace_root is not None:
        cache = {}
        for finding in payload.get("findings", []):
            _decorate_finding_sources(finding, result, settings, workspace_root, cache)
    return payload


app = create_app()
