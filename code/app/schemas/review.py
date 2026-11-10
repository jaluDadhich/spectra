"""Review request/response and stage-status schemas."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.findings import CaseStatus, CategoryAssessment, Finding
from app.schemas.scoring import QualityScore


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    INCOMPLETE = "INCOMPLETE"  # ran, but required inputs/credentials missing


class CheckStatus(str, Enum):
    COMPLETED = "COMPLETED"
    SKIPPED_NOT_APPLICABLE = "SKIPPED_NOT_APPLICABLE"
    FAILED = "FAILED"
    INCOMPLETE = "INCOMPLETE"  # e.g. external source outage, wording missing


class CheckOutcome(BaseModel):
    check_id: str  # rule_id or stage check name
    rule_version: Optional[str] = None
    status: CheckStatus
    detail: str = ""
    finding_ids: List[str] = Field(default_factory=list)


class StageResult(BaseModel):
    stage: str
    status: StageStatus
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    errors: List[str] = Field(default_factory=list)
    checks: List[CheckOutcome] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)


class DocumentInput(BaseModel):
    """One input document: either a path to a JSON file or an inline object."""

    path: Optional[str] = None
    content: Optional[Dict[str, Any]] = None


class ReviewRequest(BaseModel):
    submission_id: str
    documents: List[DocumentInput] = Field(default_factory=list)
    input_dir: Optional[str] = None
    line_of_business: Optional[str] = None
    jurisdiction: Optional[str] = None
    effective_date: Optional[str] = None
    review_date: Optional[str] = None
    profile: Optional[str] = None  # required-document profile name
    ruleset: Optional[str] = None  # ruleset name/version
    deterministic_only: bool = False
    allow_partial: bool = True  # proceed in PARTIAL mode on invalid files
    run_settings: Dict[str, Any] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: Optional[float] = None
    retries: int = 0
    errors: int = 0


class ReviewResult(BaseModel):
    run_id: str
    submission_id: str
    status: CaseStatus
    partial: bool = False
    stages: List[StageResult] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    category_assessments: List[CategoryAssessment] = Field(default_factory=list)
    checks: List[CheckOutcome] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    config_versions: Dict[str, str] = Field(default_factory=dict)
    quality_score: Optional[QualityScore] = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    created_at: str = ""
    completed_at: Optional[str] = None
