"""Deduplication and related-finding linking (spec Stage 10).

- Identical deduplication keys collapse to one finding (keeping the
  highest severity and the union of evidence).
- P5 vs N5: an annual-count alert that has no evidence beyond the claim
  records already cited by a P5 finding is merged into the P5 finding.
- P7 vs N14: an external-identity alert fully covered by a P7 finding on
  the same subject is linked rather than duplicated.
- Distinct corrective actions stay separate findings.
"""
from __future__ import annotations

from typing import Dict, List

from app.schemas.findings import Finding, Severity

_SEVERITY_ORDER = [
    Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFORMATIONAL,
]


def _more_severe(a: Finding, b: Finding) -> Finding:
    return a if _SEVERITY_ORDER.index(a.severity) <= _SEVERITY_ORDER.index(b.severity) else b


def deduplicate(findings: List[Finding]) -> List[Finding]:
    by_key: Dict[str, Finding] = {}
    for f in findings:
        existing = by_key.get(f.deduplication_key)
        if existing is None:
            by_key[f.deduplication_key] = f
            continue
        keep = _more_severe(existing, f)
        seen = {e.evidence_id for e in keep.evidence}
        for ref in f.evidence:
            if ref.evidence_id not in seen:
                keep.evidence.append(ref)
        if f.finding_id != keep.finding_id and f.finding_id not in keep.related_finding_ids:
            keep.related_finding_ids.append(f.finding_id)
        by_key[f.deduplication_key] = keep
    result = list(by_key.values())
    _merge_covered(result)
    _link_related(result)
    return result


def _merge_covered(findings: List[Finding]) -> None:
    """Merge covered annual-loss and external-scope items into primary themes."""
    def merge_into(primary_matches, secondary_matches) -> None:
        primaries = [f for f in findings if primary_matches(f)]
        secondaries = [f for f in findings if secondary_matches(f)]
        for sec in list(secondaries):
            sec_eids = {e.evidence_id for e in sec.evidence}
            for prim in primaries:
                if sec is prim:
                    continue
                prim_eids = {e.evidence_id for e in prim.evidence}
                if sec_eids and sec_eids.issubset(prim_eids):
                    if sec.finding_id not in prim.related_finding_ids:
                        prim.related_finding_ids.append(sec.finding_id)
                    findings.remove(sec)
                    secondaries.remove(sec)
                    break

    # U08 and U13 each group multiple issue subtypes. Only merge the
    # historically covered subtype when its evidence is wholly contained;
    # findings in the same unified category can still be independent.
    merge_into(
        lambda f: f.taxonomy_id == "U08"
        and f.finding_type != "ANNUAL_CLAIM_COUNT_MISMATCH",
        lambda f: f.taxonomy_id == "U08"
        and f.finding_type == "ANNUAL_CLAIM_COUNT_MISMATCH",
    )
    merge_into(
        lambda f: f.taxonomy_id == "U13"
        and not f.finding_type.startswith("EXTERNAL_RECORD_"),
        lambda f: f.taxonomy_id == "U13"
        and f.finding_type.startswith("EXTERNAL_RECORD_"),
    )
    merge_into(lambda f: f.taxonomy_id == "M5", lambda f: f.taxonomy_id == "N3")
    merge_into(lambda f: f.taxonomy_id == "M6", lambda f: f.taxonomy_id == "N8")
    merge_into(lambda f: f.taxonomy_id == "P5", lambda f: f.taxonomy_id == "N5")
    merge_into(lambda f: f.taxonomy_id == "P7", lambda f: f.taxonomy_id == "N14")


def _link_related(findings: List[Finding]) -> None:
    """Link findings sharing substantial evidence (same underlying fact
    pattern) without merging independently evidenced issues."""
    for i, a in enumerate(findings):
        eids_a = {e.evidence_id for e in a.evidence}
        for b in findings[i + 1:]:
            if a.finding_id == b.finding_id:
                continue
            eids_b = {e.evidence_id for e in b.evidence}
            if not eids_a or not eids_b:
                continue
            overlap = len(eids_a & eids_b) / max(1, min(len(eids_a), len(eids_b)))
            if overlap >= 0.5:
                if b.finding_id not in a.related_finding_ids:
                    a.related_finding_ids.append(b.finding_id)
                if a.finding_id not in b.related_finding_ids:
                    b.related_finding_ids.append(a.finding_id)
