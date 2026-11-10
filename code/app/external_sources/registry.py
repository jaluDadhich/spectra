"""Governed external source registry (spec Stage 5).

- Adapters are disabled by default and activate only with credentials +
  explicit enablement flags.
- Each registry entry records supported/unsupported facts, jurisdiction,
  permitted use, access method, freshness policy, cost, owner, licensing,
  and fallback.
- Outcome states: VERIFIED_MATCH | CONFLICT_FOUND | POSSIBLE_MATCH |
  NO_RESULT | UNAVAILABLE_OR_STALE.
- No generic web scraping. No adapter here performs network access in the
  bundled implementation; approved adapters must be supplied per carrier.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from pydantic import BaseModel, Field

from app.config import Settings


class OutcomeState(str, Enum):
    VERIFIED_MATCH = "VERIFIED_MATCH"
    CONFLICT_FOUND = "CONFLICT_FOUND"
    POSSIBLE_MATCH = "POSSIBLE_MATCH"
    NO_RESULT = "NO_RESULT"
    UNAVAILABLE_OR_STALE = "UNAVAILABLE_OR_STALE"


class SourceRegistryEntry(BaseModel):
    source_id: str
    name: str
    supported_facts: List[str] = Field(default_factory=list)
    unsupported_facts: List[str] = Field(default_factory=list)
    jurisdictions: List[str] = Field(default_factory=list)
    permitted_use: str = ""
    access_method: str = "none"  # api | batch | none
    freshness_policy: str = ""
    cost_model: str = ""
    owner: str = ""
    licensing_status: str = "not_reviewed"
    fallback: str = ""
    enabled: bool = False


class ExternalResult(BaseModel):
    source_id: str
    outcome: OutcomeState
    record_identifier: Optional[str] = None
    record_url: Optional[str] = None
    retrieved_at: Optional[str] = None
    fields: Dict[str, Any] = Field(default_factory=dict)
    match_rationale: str = ""
    limitations: List[str] = Field(default_factory=list)


class ExternalSourceAdapter(Protocol):
    entry: SourceRegistryEntry

    def lookup(self, identifiers: Dict[str, Any]) -> ExternalResult:
        ...


class DisabledAdapter:
    """Placeholder that always reports UNAVAILABLE_OR_STALE so checks are
    visibly incomplete rather than silently passing."""

    def __init__(self, entry: SourceRegistryEntry):
        self.entry = entry

    def lookup(self, identifiers: Dict[str, Any]) -> ExternalResult:
        return ExternalResult(
            source_id=self.entry.source_id,
            outcome=OutcomeState.UNAVAILABLE_OR_STALE,
            match_rationale="Adapter not configured/enabled",
            limitations=["External check incomplete — source not approved/configured"],
        )


class SourceRegistry:
    def __init__(self, entries: List[SourceRegistryEntry],
                 adapters: Dict[str, ExternalSourceAdapter]):
        self.entries = {e.source_id: e for e in entries}
        self.adapters = adapters

    def adapter_for(self, source_id: str) -> ExternalSourceAdapter:
        entry = self.entries[source_id]
        return self.adapters.get(source_id) or DisabledAdapter(entry)

    def enabled(self) -> List[str]:
        return [sid for sid, e in self.entries.items() if e.enabled]


def build_source_registry(settings: Settings) -> SourceRegistry:
    """Bundled registry. Real adapters must be implemented and approved per
    carrier; until then lookups resolve to DisabledAdapter and checks are
    reported incomplete."""
    entries = [
        SourceRegistryEntry(
            source_id="state_entity_registry",
            name="State business entity registry (per-jurisdiction)",
            supported_facts=["legal_name", "registration_status", "registration_number"],
            unsupported_facts=["operations", "premises_conditions"],
            jurisdictions=[],
            permitted_use="Entity identity verification only",
            access_method="api",
            freshness_policy="Record retrieval timestamp required; >90d is stale",
            cost_model="per-call",
            owner="carrier compliance",
            licensing_status="not_reviewed",
            fallback="Request certificate of good standing from broker",
            enabled=settings.spectra_ext_state_registry_enabled
            and settings.spectra_ext_state_registry_api_key is not None,
        ),
        SourceRegistryEntry(
            source_id="fmcsa_sms",
            name="FMCSA Safety Measurement System (commercial auto)",
            supported_facts=["usdot_status", "fleet_size", "safety_measures"],
            unsupported_facts=["driver_qualification"],
            jurisdictions=["US"],
            permitted_use="Auto liability underwriting support",
            access_method="api",
            freshness_policy="Monthly refresh; retrieval timestamp required",
            cost_model="free/public",
            owner="carrier compliance",
            licensing_status="not_reviewed",
            fallback="Request CAB report or SAFER snapshot from broker",
            enabled=settings.spectra_ext_fmcsa_enabled
            and settings.spectra_ext_fmcsa_api_key is not None,
        ),
    ]
    return SourceRegistry(entries, adapters={})
