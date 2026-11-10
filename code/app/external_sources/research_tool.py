"""Local-first public-record research with durable result caching.

Local external_records always win: when the submission includes an
external_records document this tool never sends its account data to a search
provider. Live Tavily-compatible search is strictly opt-in.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings
from app.schemas.canonical import CanonicalCase, EvidenceItem, SourceLocation
from app.storage.repository import CaseRepository


class ExternalResearchTool:
    SOURCES = ("osha", "state_registry", "news")

    def __init__(self, settings: Settings, repository: Optional[CaseRepository] = None):
        self.settings = settings
        self.repository = repository

    def search(self, case: CanonicalCase) -> Dict[str, Any]:
        """Return source results and evidence IDs, local-first then cache then live."""
        local_docs = case.documents_of_type("external_records")
        local_ids: List[str] = []
        if local_docs:
            local_doc_ids = {doc.document_id for doc in local_docs}
            for observation in case.external_observations:
                if observation.document_id not in local_doc_ids:
                    continue
                local_ids.extend(observation.field_evidence.values())
                item_id = next(iter(observation.field_evidence.values()), None)
                if item_id and item_id in case.evidence_store and observation.record_url:
                    case.evidence_store[item_id].source_location.url = observation.record_url
            return {
                "mode": "local_records",
                "sources_checked": [o.source_name for o in case.external_observations
                                     if o.document_id in local_doc_ids],
                "evidence_ids": sorted(set(local_ids)),
                "live_calls": 0,
                "reason": "external_records document supplied; account data was not sent externally",
            }

        if not case.entities:
            return {"mode": "skipped", "evidence_ids": [], "live_calls": 0,
                    "reason": "No insured identity available for public-record research"}
        insured = next((e for e in case.entities if e.entity_role == "insured"), case.entities[0])
        name = insured.legal_name or insured.normalized_name or ""
        state = next((loc.state for loc in case.locations if loc.state), case.jurisdiction or "")
        if not name:
            return {"mode": "skipped", "evidence_ids": [], "live_calls": 0,
                    "reason": "No insured name available for public-record research"}

        if not self.settings.spectra_web_search_enabled or not self.settings.spectra_web_search_api_key:
            return {"mode": "disabled", "evidence_ids": [], "live_calls": 0,
                    "reason": "Live web search is disabled or unconfigured"}

        results: Dict[str, Any] = {}
        evidence_ids: List[str] = []
        live_calls = cache_hits = 0
        errors: List[str] = []
        for source in self.SOURCES:
            query = self._query(source, name, state)
            cache_key = hashlib.sha256(
                f"{self.settings.spectra_web_search_url}|{source}|{query}".lower().encode()
            ).hexdigest()
            try:
                cached = self.repository.get_external_cache(cache_key) if self.repository else None
                if cached is not None:
                    payload = cached
                    cache_hits += 1
                else:
                    live_calls += 1
                    payload = self._live_search(source, query)
                    if self.repository:
                        self.repository.put_external_cache(
                            cache_key, source, payload,
                            self.settings.spectra_web_search_cache_ttl_hours)
            except Exception as exc:
                errors.append(f"{source}: {type(exc).__name__}: {exc}")
                results[source] = []
                continue
            hits = payload.get("results", []) if isinstance(payload, dict) else []
            results[source] = hits
            for i, hit in enumerate(hits):
                if not isinstance(hit, dict):
                    continue
                title = str(hit.get("title") or "Untitled result")
                url = str(hit.get("url") or "")
                content = str(hit.get("content") or hit.get("snippet") or "")
                retrieved_at = (hit.get("retrieved_at") or payload.get("_cache_retrieved_at")
                                or datetime.now(timezone.utc).isoformat())
                published_date = hit.get("published_date") or hit.get("published_at")
                evidence_id = "EV-WEB-" + hashlib.sha256(
                    f"{source}|{url}|{title}".encode()).hexdigest()[:16].upper()
                case.evidence_store[evidence_id] = EvidenceItem(
                    evidence_id=evidence_id,
                    document_id=f"EXT-WEB-{source.upper()}",
                    document_name=f"Web search: {source}",
                    field_path=f"web_search.{source}[{i}]",
                    source_location=SourceLocation(available=bool(url), url=url or None),
                    raw_value={"source": source, "title": title, "url": url,
                               "content": content, "published_date": published_date,
                               "retrieved_at": retrieved_at},
                    normalized_value={"source": source, "title": title, "url": url,
                                     "content": content, "published_date": published_date,
                                     "retrieved_at": retrieved_at},
                    snippet=(f"{title} — {content[:500]} — published={published_date} "
                             f"retrieved={retrieved_at} — {url}"),
                    extraction_confidence=None,
                )
                evidence_ids.append(evidence_id)
        return {"mode": "partial_live_search" if errors else "live_search", "results": results,
                "evidence_ids": evidence_ids, "live_calls": live_calls,
                "cache_hits": cache_hits, "errors": errors}

    @staticmethod
    def _query(source: str, name: str, state: str) -> str:
        if source == "osha":
            return f'OSHA inspections violations establishment "{name}" {state}'.strip()
        if source == "state_registry":
            return f'state business entity registry registration status "{name}" {state}'.strip()
        return f'"{name}" {state} business operations news'.strip()

    def _live_search(self, source: str, query: str) -> Dict[str, Any]:
        key = self.settings.spectra_web_search_api_key
        if key is None:
            return {"results": [], "error": "search key unavailable"}
        payload: Dict[str, Any] = {
            "api_key": key.get_secret_value(), "query": query,
            "search_depth": "basic", "max_results": 5,
        }
        if source == "osha":
            payload["include_domains"] = ["osha.gov"]
        elif source == "news":
            payload["topic"] = "news"
        response = httpx.post(
            self.settings.spectra_web_search_url,
            json=payload,
            timeout=self.settings.openai_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("results", []), list):
            raise ValueError("Web search provider returned an unsupported response shape")
        return data
