"""Submission-quality scoring schema.

The score describes the quality/completeness of the submitted data and the
reliability of the review — it is decision support, not a bind/decline
signal. Most components are deterministic; issue-severity points are LLM
scored by unique group with a deterministic fallback and an explicit source.
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class ScoreComponent(BaseModel):
    name: str
    points_lost: float = Field(ge=0)
    detail: str
    source: str = "deterministic"


class QualityScore(BaseModel):
    score: float = Field(ge=0, le=100)
    grade: str  # A | B | C | D | F
    components: List[ScoreComponent] = Field(default_factory=list)
    computed_at: str = ""
    method: str = "spectra-quality-score@2.0.0"
