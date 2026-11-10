"""Canonical internal data model (spec section 4, Stage 2).

Every extracted value carries evidence references (evidence IDs) pointing
at EvidenceItems that preserve raw value, normalized value, unit, period,
and source location. Source locations are only populated when the input
JSON actually supplies provenance — they are never fabricated.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SourceLocation(BaseModel):
    page: Optional[int] = None
    sheet: Optional[str] = None
    row: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    column: Optional[str] = None
    cell: Optional[str] = None
    record_id: Optional[str] = None
    url: Optional[str] = None
    json_path: Optional[str] = None
    available: bool = False  # False = provenance not supplied; never fabricate


class EvidenceItem(BaseModel):
    evidence_id: str
    document_id: str
    document_name: Optional[str] = None
    # Workspace-relative path to the original PDF/XLSX when it is available.
    # Never point a reviewer at the extracted JSON representation.
    source_file: Optional[str] = None
    field_path: str  # canonical field path, e.g. "claims[CLM-1].paid"
    source_location: SourceLocation = Field(default_factory=SourceLocation)
    raw_value: Any = None
    normalized_value: Any = None
    unit: Optional[str] = None
    period: Optional[str] = None
    snippet: Optional[str] = None
    extraction_confidence: Optional[float] = None


class RecordBase(BaseModel):
    """Base for typed canonical records.

    field_evidence maps canonical field name -> evidence_id, giving every
    compared value a precise, inspectable citation.
    """

    model_config = ConfigDict(extra="allow")

    record_id: str
    document_id: str
    field_evidence: Dict[str, str] = Field(default_factory=dict)


class EntityRecord(RecordBase):
    legal_name: Optional[str] = None
    normalized_name: Optional[str] = None
    dba: List[str] = Field(default_factory=list)
    parent_name: Optional[str] = None
    subsidiaries: List[str] = Field(default_factory=list)
    fein: Optional[str] = None
    registration_number: Optional[str] = None
    usdot_number: Optional[str] = None
    entity_role: str = "insured"  # insured | parent | subsidiary | external


class LocationRecord(RecordBase):
    location_id: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    normalized_address: Optional[str] = None
    building_value: Optional[float] = None
    contents_value: Optional[float] = None
    tiv: Optional[float] = None
    square_footage: Optional[float] = None


class VehicleRecord(RecordBase):
    unit_id: Optional[str] = None
    vin: Optional[str] = None
    status: Optional[str] = None  # active | inactive | sold ...
    gvwr: Optional[float] = None
    vehicle_type: Optional[str] = None
    garaging_state: Optional[str] = None
    garaging_address: Optional[str] = None
    assigned_driver_id: Optional[str] = None
    cost_new: Optional[float] = None


class DriverRecord(RecordBase):
    driver_id: Optional[str] = None
    name: Optional[str] = None
    license_class: Optional[str] = None
    cdl_endorsements: List[str] = Field(default_factory=list)
    mvr_review_date: Optional[str] = None
    hire_date: Optional[str] = None


class ClaimRecord(RecordBase):
    claim_id: Optional[str] = None
    line_of_business: Optional[str] = None
    loss_date: Optional[str] = None
    paid: Optional[float] = None
    reserves: Optional[float] = None
    incurred_reported: Optional[float] = None
    status: Optional[str] = None
    valuation_date: Optional[str] = None
    policy_year: Optional[str] = None


class WCPayrollRow(RecordBase):
    class_code: Optional[str] = None
    state: Optional[str] = None
    location_id: Optional[str] = None
    payroll: Optional[float] = None
    full_time_count: Optional[int] = None
    part_time_count: Optional[int] = None
    rate: Optional[float] = None  # per $100 payroll unless profile says otherwise
    effective_date: Optional[str] = None


class RatingFactorRecord(RecordBase):
    factor_type: str  # e.g. "experience_mod", "class_rate", "schedule_credit"
    value: Optional[float] = None
    effective_date: Optional[str] = None
    state: Optional[str] = None
    class_code: Optional[str] = None
    source_authority: Optional[str] = None  # e.g. "bureau", "carrier", "workbook"


class QuoteTermRecord(RecordBase):
    term_type: str  # limit | deductible | sublimit | form | endorsement | exclusion | operation | premium | factor
    coverage: Optional[str] = None
    name: Optional[str] = None
    value: Any = None
    unit: Optional[str] = None
    scope: Optional[str] = None  # e.g. per-occurrence, aggregate, location id


class ExposureRecord(RecordBase):
    exposure_type: str  # payroll | sales | square_footage | vehicle_count | cargo_value | operations
    value: Any = None
    unit: Optional[str] = None
    period: Optional[str] = None
    location_id: Optional[str] = None
    description: Optional[str] = None


class ExternalObservation(RecordBase):
    source_name: str
    outcome_state: Literal[
        "VERIFIED_MATCH", "CONFLICT_FOUND", "POSSIBLE_MATCH",
        "NO_RESULT", "UNAVAILABLE_OR_STALE",
    ]
    subject: Optional[str] = None  # what was checked (entity, premises, vehicle)
    record_identifier: Optional[str] = None
    record_url: Optional[str] = None
    retrieved_at: Optional[str] = None
    record_date: Optional[str] = None
    match_rationale: Optional[str] = None
    limitations: List[str] = Field(default_factory=list)
    fields: Dict[str, Any] = Field(default_factory=dict)


class ChecklistItem(RecordBase):
    item: str
    status: Optional[str] = None  # attached | missing | not_applicable ...
    note: Optional[str] = None


class NarrativeRecord(RecordBase):
    """Free-text content (broker email, operations description). Untrusted."""

    text: str
    subject: Optional[str] = None


class DocumentVersionLink(BaseModel):
    document_id: str
    supersedes: Optional[str] = None
    version: Optional[str] = None
    change_log: Optional[str] = None


class CanonicalDocument(BaseModel):
    document_id: str
    document_type: str  # registered type or "unsupported"
    declared_document_type: Optional[str] = None
    document_name: Optional[str] = None
    issuer: Optional[str] = None
    version: Optional[str] = None
    received_at: Optional[str] = None
    effective_period: Optional[Dict[str, Optional[str]]] = None
    source_hash: Optional[str] = None
    content_hash: Optional[str] = None  # computed fingerprint of data
    recognized: bool = True
    generated_id: bool = False  # True when document_id was assigned internally
    validation_errors: List[str] = Field(default_factory=list)
    raw: Dict[str, Any] = Field(default_factory=dict)  # immutable input data


class CanonicalCase(BaseModel):
    """Normalized, evidence-linked view of one submission under review."""

    submission_id: str
    run_id: str
    line_of_business: Optional[str] = None
    jurisdiction: Optional[str] = None
    effective_date: Optional[str] = None
    review_date: Optional[str] = None

    documents: List[CanonicalDocument] = Field(default_factory=list)
    evidence_store: Dict[str, EvidenceItem] = Field(default_factory=dict)

    entities: List[EntityRecord] = Field(default_factory=list)
    locations: List[LocationRecord] = Field(default_factory=list)
    vehicles: List[VehicleRecord] = Field(default_factory=list)
    drivers: List[DriverRecord] = Field(default_factory=list)
    claims: List[ClaimRecord] = Field(default_factory=list)
    wc_payroll_rows: List[WCPayrollRow] = Field(default_factory=list)
    rating_factors: List[RatingFactorRecord] = Field(default_factory=list)
    quote_terms: List[QuoteTermRecord] = Field(default_factory=list)
    exposures: List[ExposureRecord] = Field(default_factory=list)
    external_observations: List[ExternalObservation] = Field(default_factory=list)
    checklist_items: List[ChecklistItem] = Field(default_factory=list)
    narratives: List[NarrativeRecord] = Field(default_factory=list)
    version_links: List[DocumentVersionLink] = Field(default_factory=list)

    def evidence(self, evidence_id: str) -> Optional[EvidenceItem]:
        return self.evidence_store.get(evidence_id)

    def documents_of_type(self, doc_type: str) -> List[CanonicalDocument]:
        return [d for d in self.documents if d.document_type == doc_type]

    def has_document_type(self, doc_type: str) -> bool:
        return any(d.document_type == doc_type for d in self.documents)
