"""Required-document profiles (spec Stage 1).

Profiles are configuration. Missing-document findings are generated only
when a requirement is configured and applicable to the line of business.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class DocumentRequirement(BaseModel):
    document_type: str
    required: bool = True
    lines_of_business: List[str] = Field(default_factory=list)  # empty = all
    freshness_days: Optional[int] = None  # staleness rule where applicable
    rationale: str = ""


class DocumentProfile(BaseModel):
    profile_id: str
    version: str
    description: str = ""
    requirements: List[DocumentRequirement] = Field(default_factory=list)

    def required_types(self, line_of_business: Optional[str]) -> Dict[str, DocumentRequirement]:
        result: Dict[str, DocumentRequirement] = {}
        for req in self.requirements:
            if not req.required:
                continue
            if req.lines_of_business and line_of_business and line_of_business not in req.lines_of_business:
                continue
            result[req.document_type] = req
        return result


_DEFAULT_PROFILE: Optional[DocumentProfile] = None


def default_profile() -> DocumentProfile:
    global _DEFAULT_PROFILE
    if _DEFAULT_PROFILE is None:
        profiles_dir = Path(__file__).resolve().parents[2] / "profiles"
        _DEFAULT_PROFILE = load_profile("commercial_package_default", profiles_dir)
    return _DEFAULT_PROFILE


def load_profile(name: str, profile_dir: Optional[Path] = None) -> DocumentProfile:
    from app.config import get_settings

    directory = profile_dir or get_settings().spectra_profile_dir
    path = Path(directory) / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Document profile not found: {path}")
    return DocumentProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
