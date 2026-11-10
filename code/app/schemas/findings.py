"""Finding data contract (spec section 8)."""
from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from app.schemas.canonical import SourceLocation


class FindingClassification(str, Enum):
    MANIFESTED_CATEGORY = "MANIFESTED_CATEGORY"
    NON_MANIFESTED_DISCOVERY = "NON_MANIFESTED_DISCOVERY"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    SYSTEM_CHECK_FAILURE = "SYSTEM_CHECK_FAILURE"


class FindingStatus(str, Enum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    RESOLVED = "RESOLVED"
    WAIVED = "WAIVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    REJECTED_FALSE_POSITIVE = "REJECTED_FALSE_POSITIVE"


class ConclusionState(str, Enum):
    CONFIRMED_DATA_CONFLICT = "CONFIRMED_DATA_CONFLICT"
    POSSIBLE_MISMATCH = "POSSIBLE_MISMATCH"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    NO_DISCREPANCY_FOUND = "NO_DISCREPANCY_FOUND"
    CHECK_INCOMPLETE = "CHECK_INCOMPLETE"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFORMATIONAL = "INFORMATIONAL"


class IssueNature(str, Enum):
    """How the evidence describes the issue, beyond its catalog category."""

    DATA_MISMATCH = "DATA_MISMATCH"
    EXTERNAL_SOURCE_OF_TRUTH = "EXTERNAL_SOURCE_OF_TRUTH"
    EXTERNAL_CONFLICT = "EXTERNAL_CONFLICT"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    EXPOSURE_OMISSION = "EXPOSURE_OMISSION"
    DATA_QUALITY = "DATA_QUALITY"
    CHANGE_OVER_TIME = "CHANGE_OVER_TIME"
    UNSUPPORTED_DECLARATION = "UNSUPPORTED_DECLARATION"
    COVERAGE_OR_LIMITATION = "COVERAGE_OR_LIMITATION"
    DUPLICATE_OR_OVERLAP = "DUPLICATE_OR_OVERLAP"
    OTHER = "OTHER"


class MaterialityBand(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class ConfidenceBasis(str, Enum):
    RULE_DETERMINISTIC = "RULE_DETERMINISTIC"
    MODEL_SUGGESTED = "MODEL_SUGGESTED"
    HUMAN_ADJUDICATED = "HUMAN_ADJUDICATED"


class UncertaintyState(str, Enum):
    CONFIRMED_DATA_CONFLICT = "CONFIRMED_DATA_CONFLICT"
    POSSIBLE_MISMATCH = "POSSIBLE_MISMATCH"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class DispositionAction(str, Enum):
    CONFIRM = "CONFIRM"
    REJECT_FALSE_POSITIVE = "REJECT_FALSE_POSITIVE"
    RESOLVE = "RESOLVE"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    REFER_SPECIALIST = "REFER_SPECIALIST"
    WAIVE = "WAIVE"
    OVERRIDE_STATUS = "OVERRIDE_STATUS"
    MARK_NOT_APPLICABLE = "MARK_NOT_APPLICABLE"


class Materiality(BaseModel):
    band: MaterialityBand = MaterialityBand.UNKNOWN
    rationale: str = ""
    impact_estimate: Optional[float] = None  # only with approved rating inputs


class Confidence(BaseModel):
    value: Optional[float] = None  # null unless empirically calibrated
    basis: ConfidenceBasis = ConfidenceBasis.RULE_DETERMINISTIC
    calibrated: bool = False
    explanation: str = ""


class EvidenceRef(BaseModel):
    evidence_id: str
    document_id: str
    document_name: Optional[str] = None
    source_file: Optional[str] = None
    source_location: SourceLocation = Field(default_factory=SourceLocation)
    raw_value: Any = None
    normalized_value: Any = None
    unit: Optional[str] = None
    period: Optional[str] = None
    snippet: Optional[str] = None


class ComparisonSource(BaseModel):
    """One document's contribution to a cross-document comparison."""

    document_id: str
    document_name: Optional[str] = None
    label: str = ""
    value: Any = None
    status: Optional[str] = None  # PRESENT | NOT_LISTED | COMPARED | EXPECTED | CONFLICTING | HISTORICAL
    evidence_ids: List[str] = Field(default_factory=list)


class CalculationInput(BaseModel):
    label: str
    value: Any = None
    evidence_ids: List[str] = Field(default_factory=list)


class CalculationDetail(BaseModel):
    label: str
    formula: str
    result: Any = None
    rationale: str = ""
    inputs: List[CalculationInput] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)


class Comparison(BaseModel):
    values_compared: List[Any] = Field(default_factory=list)
    sources: List[ComparisonSource] = Field(default_factory=list)
    calculation: Optional[CalculationDetail] = None
    delta: Optional[float] = None
    delta_pct: Optional[float] = None
    delta_formula: Optional[str] = None
    tolerance: Optional[float] = None
    tolerance_source: Optional[str] = None
    entity_alignment: str = "UNRESOLVED"  # MATCHED | PARTIAL | UNRESOLVED
    period_alignment: str = "UNRESOLVED"


class ReviewerDisposition(BaseModel):
    action: DispositionAction
    reviewer_id: str
    rationale: str
    timestamp: str
    prior_status: str
    new_status: str
    linked_evidence: List[str] = Field(default_factory=list)
    rule_versions: dict = Field(default_factory=dict)


class Finding(BaseModel):
    finding_id: str
    submission_id: str
    finding_type: str
    taxonomy_id: str
    rule_id: str
    rule_version: str
    title: str
    classification: FindingClassification
    status: FindingStatus = FindingStatus.OPEN
    conclusion_state: ConclusionState
    severity: Severity = Severity.MEDIUM
    severity_rationale: Optional[str] = None
    issue_nature: Optional[IssueNature] = None
    issue_group_key: Optional[str] = None
    issue_group_title: Optional[str] = None
    materiality: Materiality = Field(default_factory=Materiality)
    confidence: Confidence = Field(default_factory=Confidence)
    uncertainty_reason: Optional[str] = None
    evidence: List[EvidenceRef] = Field(default_factory=list)
    comparison: Optional[Comparison] = None
    possible_consequence: str = ""
    recommended_action: str = ""
    estimated_impact: Optional[float] = None
    estimated_impact_assumptions: List[str] = Field(default_factory=list)
    related_finding_ids: List[str] = Field(default_factory=list)
    deduplication_key: str = ""
    blocking: bool = False
    explanation: Optional[str] = None  # GPT-written, post-validation only
    created_at: str = ""
    updated_at: str = ""
    reviewer_disposition: Optional[ReviewerDisposition] = None
    disposition_history: List[ReviewerDisposition] = Field(default_factory=list)


class CaseStatus(str, Enum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    HOLD = "HOLD"
    ERROR_INCOMPLETE = "ERROR/INCOMPLETE"


class CategoryStatus(str, Enum):
    """Per-category outcome contract from the unified issue register."""

    CONFIRMED = "CONFIRMED"
    POTENTIAL = "POTENTIAL"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NO_DISCREPANCY = "NO_DISCREPANCY"


class CategoryAssessment(BaseModel):
    """One entry per unified issue category (U01-U14) for a submission."""

    issue_id: str
    category: str
    primary_area: str = ""
    status: CategoryStatus
    rationale: str = ""
    finding_ids: List[str] = Field(default_factory=list)
