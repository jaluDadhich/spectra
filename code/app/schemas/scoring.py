"""Submission-quality scoring schema.

Quality is presented as five deterministic underwriting pillars. Each pillar
has a 0–100 score and contributes to the weighted overall score.
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class ScoreComponent(BaseModel):
    name: str
    points_lost: float = Field(ge=0)
    detail: str
    source: str = "deterministic"


class QualityDimension(BaseModel):
    key: str
    title: str
    score: float = Field(ge=0, le=100)
    weight_pct: float = Field(ge=0, le=100)
    points_lost: float = Field(ge=0, le=100)
    issue_count: int = Field(ge=0)
    finding_count: int = Field(ge=0)
    detail: str = ""


class QualityScore(BaseModel):
    score: float = Field(ge=0, le=100)
    grade: str  # A | B | C | D | F
    dimensions: List[QualityDimension] = Field(default_factory=list)
    components: List[ScoreComponent] = Field(default_factory=list)
    formula: str = ""
    deduction_policy: str = ""
    computed_at: str = ""
    method: str = "spectra-underwriting-quality@3.0.0"
