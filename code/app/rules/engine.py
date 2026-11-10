"""Deterministic rule engine (spec Stages 3-8 + section 9)."""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from app.catalog.profiles import DocumentProfile
from app.findings.builder import FindingBuilder, FindingDraft
from app.rules.registry import RuleDefinition, RuleRegistry
from app.schemas.canonical import CanonicalCase
from app.schemas.findings import Comparison, Finding
from app.schemas.review import CheckOutcome, CheckStatus

logger = logging.getLogger("spectra.rules")


class RuleContext:
    def __init__(self, case: CanonicalCase, rule: RuleDefinition,
                 profile: Optional[DocumentProfile], builder: FindingBuilder):
        self.case = case
        self.rule = rule
        self.profile = profile
        self.builder = builder

    def tolerance(self) -> Tuple[Optional[float], Optional[float], str]:
        t = self.rule.tolerance
        return t.absolute, t.percent, t.source

    def within_tolerance(self, delta: float, base: Optional[float] = None) -> bool:
        abs_tol, pct_tol, _ = self.tolerance()
        if abs_tol is not None and abs(delta) <= abs_tol:
            return True
        if pct_tol is not None and base:
            return abs(delta) <= abs(base) * pct_tol / 100.0
        return abs_tol is None and pct_tol is None and delta == 0

    def comparison(self, values: list, delta: Optional[float] = None,
                   formula: Optional[str] = None, base: Optional[float] = None,
                   entity_alignment: str = "UNRESOLVED",
                   period_alignment: str = "UNRESOLVED") -> Comparison:
        abs_tol, pct_tol, source = self.tolerance()
        delta_pct = None
        if delta is not None and base:
            delta_pct = round(delta / abs(base) * 100.0, 2) if base else None
        return Comparison(
            values_compared=values, delta=delta, delta_pct=delta_pct,
            delta_formula=formula,
            tolerance=abs_tol if abs_tol is not None else pct_tol,
            tolerance_source=source,
            entity_alignment=entity_alignment, period_alignment=period_alignment,
        )


class RuleEngine:
    def __init__(self, registry: RuleRegistry, profile: Optional[DocumentProfile] = None):
        self.registry = registry
        self.profile = profile

    def run(self, case: CanonicalCase) -> Tuple[List[Finding], List[CheckOutcome], List[str]]:
        """Returns (findings, check outcomes, rejected-draft notes)."""
        builder = FindingBuilder(case)
        findings: List[Finding] = []
        checks: List[CheckOutcome] = []
        rejected: List[str] = []
        for rule in self.registry.enabled_rules():
            if not self._applicable(rule, case):
                checks.append(CheckOutcome(
                    check_id=rule.rule_id, rule_version=rule.version,
                    status=CheckStatus.SKIPPED_NOT_APPLICABLE,
                    detail="Applicability conditions not satisfied"))
                continue
            ctx = RuleContext(case, rule, self.profile, builder)
            try:
                drafts = self.registry.handlers[rule.handler](ctx)
            except Exception as exc:  # rule failure must not crash the run
                logger.exception("Rule %s failed", rule.rule_id)
                checks.append(CheckOutcome(
                    check_id=rule.rule_id, rule_version=rule.version,
                    status=CheckStatus.FAILED, detail=f"{type(exc).__name__}: {exc}"))
                continue
            finding_ids: List[str] = []
            for draft in drafts:
                finding = builder.build(draft)
                if finding is None:
                    rejected.append(
                        f"{rule.rule_id}: draft '{draft.title}' rejected — invalid evidence reference")
                    continue
                findings.append(finding)
                finding_ids.append(finding.finding_id)
            checks.append(CheckOutcome(
                check_id=rule.rule_id, rule_version=rule.version,
                status=CheckStatus.COMPLETED,
                detail=f"{len(finding_ids)} finding(s)",
                finding_ids=finding_ids))
        return findings, checks, rejected

    def _applicable(self, rule: RuleDefinition, case: CanonicalCase) -> bool:
        app = rule.applicability
        if app.lines_of_business:
            if not case.line_of_business or case.line_of_business not in app.lines_of_business:
                return False
        present = {d.document_type for d in case.documents}
        if app.required_document_types_all and not set(app.required_document_types_all).issubset(present):
            return False
        if app.required_document_types_any and not set(app.required_document_types_any) & present:
            return False
        return True
