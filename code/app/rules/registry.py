"""Versioned, configuration-driven rule registry (spec section 9).

Rule metadata (IDs, versions, severity, tolerances, applicability) lives in
versioned JSON rulesets under rulesets/. Rule *logic* lives in versioned
Python handlers. Startup validation fails loudly if config and handlers
disagree.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.findings import Severity


class Applicability(BaseModel):
    lines_of_business: List[str] = Field(default_factory=list)  # empty = all
    required_document_types_all: List[str] = Field(default_factory=list)
    required_document_types_any: List[str] = Field(default_factory=list)


class Tolerance(BaseModel):
    absolute: Optional[float] = None
    percent: Optional[float] = None
    source: str = "ruleset configuration"


class RuleDefinition(BaseModel):
    rule_id: str
    version: str
    taxonomy_id: str
    handler: str
    title: str
    description: str = ""
    rationale: str = ""
    severity: Severity = Severity.MEDIUM
    blocking: bool = False
    enabled: bool = True
    applicability: Applicability = Field(default_factory=Applicability)
    tolerance: Tolerance = Field(default_factory=Tolerance)
    action_template: str = ""
    owner: str = "spectra-core"


class Ruleset(BaseModel):
    ruleset_id: str
    version: str
    description: str = ""
    rules: List[RuleDefinition] = Field(default_factory=list)


HandlerFn = Callable[..., list]


class RuleRegistry:
    def __init__(self, ruleset: Ruleset, handlers: Dict[str, HandlerFn]):
        self.ruleset = ruleset
        self.handlers = handlers
        self.validate()

    def validate(self) -> None:
        defined = {r.handler for r in self.ruleset.rules}
        missing_impl = defined - set(self.handlers)
        if missing_impl:
            raise ValueError(f"Ruleset references handlers with no implementation: {sorted(missing_impl)}")
        ids = [r.rule_id for r in self.ruleset.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate rule_id in ruleset")
        orphans = set(self.handlers) - defined
        if orphans:
            raise ValueError(f"Handlers not present in ruleset config: {sorted(orphans)}")

    def enabled_rules(self) -> List[RuleDefinition]:
        return [r for r in self.ruleset.rules if r.enabled]

    def get(self, rule_id: str) -> Optional[RuleDefinition]:
        for r in self.ruleset.rules:
            if r.rule_id == rule_id:
                return r
        return None


def load_registry(ruleset_name: str = "default", ruleset_dir: Optional[Path] = None) -> RuleRegistry:
    from app.config import get_settings
    from app.rules import handlers as handler_module

    directory = ruleset_dir or get_settings().spectra_ruleset_dir
    path = Path(directory) / f"{ruleset_name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Ruleset not found: {path}")
    ruleset = Ruleset.model_validate(json.loads(path.read_text(encoding="utf-8")))
    handlers = handler_module.all_handlers()
    return RuleRegistry(ruleset, handlers)
