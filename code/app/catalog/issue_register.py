"""Unified account-agnostic issue register (U01-U14).

The register is the canonical, exclusive issue taxonomy for Spectra.
Every reported finding maps to exactly one U-category, and every
submission is evaluated against all 14 categories with an explicit
status (CONFIRMED / POTENTIAL / NOT_ASSESSABLE / NOT_APPLICABLE /
NO_DISCREPANCY).

The register is configuration: detection logic lives in rule handlers
and LLM prompts; this module only loads and validates the catalogue.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

REGISTER_FILENAME = "unified_issue_register.json"


class CategoryApplicability(BaseModel):
    document_types_any: List[str] = Field(default_factory=list)
    record_kinds_any: List[str] = Field(default_factory=list)


class IssueCategory(BaseModel):
    id: str
    title: str
    primary_area: str = ""
    summary: str = ""
    source_documents: List[str] = Field(default_factory=list)
    rule_ids: List[str] = Field(default_factory=list)
    applicability: CategoryApplicability = Field(default_factory=CategoryApplicability)
    typical_finding: str = ""
    must_not_claim: Optional[str] = None
    detection_requirements: List[str] = Field(default_factory=list)


class IssueRegister(BaseModel):
    catalog_id: str
    version: str
    scope: str = ""
    source: str = ""
    policy: List[str] = Field(default_factory=list)
    status_definitions: Dict[str, str] = Field(default_factory=dict)
    issues: List[IssueCategory] = Field(default_factory=list)

    def validate_integrity(self) -> None:
        if self.catalog_id != "unified_account_agnostic_underwriting_issue_register":
            raise ValueError(f"Unexpected issue register catalog_id: {self.catalog_id}")
        ids = [i.id for i in self.issues]
        expected = [f"U{n:02d}" for n in range(1, 15)]
        if ids != expected:
            raise ValueError(
                f"Issue register must contain exactly U01..U14 in order; got {ids}")
        missing_requirements = [i.id for i in self.issues if not i.detection_requirements]
        if missing_requirements:
            raise ValueError(
                "Issue register is missing detection requirements for: "
                + ", ".join(missing_requirements))

    def category(self, issue_id: str) -> Optional[IssueCategory]:
        for issue in self.issues:
            if issue.id == issue_id:
                return issue
        return None

    @property
    def issue_ids(self) -> List[str]:
        return [i.id for i in self.issues]

    def rules_by_category(self) -> Dict[str, List[str]]:
        return {i.id: list(i.rule_ids) for i in self.issues}


def load_register(ruleset_dir: Optional[Path] = None) -> IssueRegister:
    from app.config import get_settings

    directory = Path(ruleset_dir) if ruleset_dir else Path(get_settings().spectra_ruleset_dir)
    path = directory / REGISTER_FILENAME
    if not path.exists():
        # Fall back to the bundled register so tests embedded in packages work.
        path = Path(__file__).resolve().parents[2] / "rulesets" / REGISTER_FILENAME
    register = IssueRegister.model_validate(json.loads(path.read_text(encoding="utf-8")))
    register.validate_integrity()
    return register
