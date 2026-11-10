"""Evidence-pack generation and export rendering (spec Stage 11 / section 12).

Summaries are derived only from validated finding objects and check
outcomes — never from free-form model text.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict

from app.schemas.review import ReviewResult


def build_export_json(result: ReviewResult) -> Dict[str, Any]:
    """Structured export: status, checks, ranked findings with full evidence."""
    return result.model_dump(mode="json")


def render_csv(result: ReviewResult) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "finding_id", "taxonomy_id", "rule_id", "rule_version", "title",
        "classification", "status", "conclusion_state", "issue_nature", "severity",
        "severity_rationale", "calculated_value", "calculation_formula",
        "calculation_rationale", "calculation_inputs",
        "materiality_band", "confidence_basis", "evidence_ids",
        "documents", "citations", "recommended_action",
    ])
    for f in result.findings:
        writer.writerow([
            f.finding_id, f.taxonomy_id, f.rule_id, f.rule_version, f.title,
            f.classification.value, f.status.value, f.conclusion_state.value,
            f.issue_nature.value if f.issue_nature else "",
            f.severity.value, f.severity_rationale or "",
            (f.comparison.calculation.result if f.comparison and f.comparison.calculation else ""),
            (f.comparison.calculation.formula if f.comparison and f.comparison.calculation else ""),
            (f.comparison.calculation.rationale if f.comparison and f.comparison.calculation else ""),
            ("; ".join(f"{item.label}={item.value}" for item in f.comparison.calculation.inputs)
             if f.comparison and f.comparison.calculation else ""),
            f.materiality.band.value, f.confidence.basis.value,
            ";".join(e.evidence_id for e in f.evidence),
            ";".join(sorted({_source_label(e) for e in f.evidence})),
            ";".join(sorted({_citation_location(e) for e in f.evidence})),
            f.recommended_action,
        ])
    return buf.getvalue()


def render_markdown_report(result: ReviewResult) -> str:
    lines = [
        f"# Spectra Review Report — {result.submission_id}",
        "",
        f"- Run ID: `{result.run_id}`",
        f"- Overall status: **{result.status.value}**",
        f"- Partial run: {'yes' if result.partial else 'no'}",
        f"- Created: {result.created_at}  Completed: {result.completed_at}",
        f"- Configuration: " + ", ".join(
            f"{k}={v}" for k, v in result.config_versions.items()),
        "",
        "## Check summary",
        "",
    ]
    counts: Dict[str, int] = {}
    for c in result.checks:
        counts[c.status.value] = counts.get(c.status.value, 0) + 1
    for status, count in sorted(counts.items()):
        lines.append(f"- {status}: {count}")
    if result.category_assessments:
        lines += ["", "## Issue category assessments (U01-U14)", ""]
        for a in result.category_assessments:
            linked = f" — findings: {', '.join(a.finding_ids)}" if a.finding_ids else ""
            lines.append(
                f"- **{a.issue_id}** {a.category}: **{a.status.value}** — "
                f"{a.rationale}{linked}")
    if result.errors:
        lines += ["", "## Processing notes / errors", ""]
        lines += [f"- {e}" for e in result.errors]
    lines += ["", f"## Findings ({len(result.findings)})", ""]
    for f in result.findings:
        title = _finding_text(f.title, f.evidence)
        lines += [
            f"### [{f.severity.value}] {title}",
            "",
            f"- Finding ID: `{f.finding_id}` | Taxonomy: {f.taxonomy_id} | "
            f"Rule: {f.rule_id} v{f.rule_version}",
            f"- Status: {f.status.value} | Conclusion: {f.conclusion_state.value} | "
            f"Issue nature: {f.issue_nature.value if f.issue_nature else 'UNCLASSIFIED'} | "
            f"Materiality: {f.materiality.band.value}",
            f"- Severity rationale: {f.severity_rationale or 'Deterministic fallback / not assessed by GPT'}",
            f"- Materiality rationale: {f.materiality.rationale or 'n/a'}",
        ]
        if f.uncertainty_reason:
            lines.append(f"- Uncertainty: {f.uncertainty_reason}")
        if f.comparison:
            cmp = f.comparison
            if cmp.sources:
                lines.append("- Source comparison:")
                for source in cmp.sources:
                    status = f" ({source.status})" if source.status else ""
                    lines.append(
                        f"  - {source.label or _source_label_from_name(source.document_name, source.document_id)}: "
                        f"{source.value!r}{status}")
            lines.append(
                f"- Comparison: values={cmp.values_compared} delta={cmp.delta} "
                f"({cmp.delta_pct}%) formula={_finding_text(cmp.delta_formula or '', f.evidence)} "
                f"tolerance={cmp.tolerance} ({cmp.tolerance_source})")
            lines.append(
                f"- Alignment: entity={cmp.entity_alignment} period={cmp.period_alignment}")
            if cmp.calculation:
                calc = cmp.calculation
                lines += [
                    "- Calculation:",
                    f"  - Formula: {_finding_text(calc.formula, f.evidence)}",
                    f"  - Result: {calc.result!r}",
                    f"  - Why applied: {_finding_text(calc.rationale, f.evidence)}",
                ]
                for item in calc.inputs:
                    evidence_refs = ", ".join(item.evidence_ids) or "no citation supplied"
                    lines.append(
                        f"  - Input: {item.label} = {item.value!r} "
                        f"(citations: {evidence_refs})")
                for limitation in calc.limitations:
                    lines.append(f"  - Limitation: {limitation}")
        if f.explanation:
            lines += ["", f"> {_finding_text(f.explanation, f.evidence)}"]
        lines += ["", f"**Why it matters:** {_finding_text(f.possible_consequence, f.evidence)}", "",
                  f"**Recommended action:** {f.recommended_action}", "",
                  "**Evidence:**", ""]
        if not f.evidence:
            lines.append("- (no field-level evidence; configuration/profile-based finding)")
        for e in f.evidence:
            loc_text = _citation_location(e)
            lines.append(
                f"- `{e.evidence_id}` ({_source_label(e)}): raw={e.raw_value!r} "
                f"normalized={e.normalized_value!r} unit={e.unit} period={e.period} "
                f"[{loc_text}]")
        if f.reviewer_disposition:
            d = f.reviewer_disposition
            lines += ["", f"**Reviewer disposition:** {d.action.value} by {d.reviewer_id} "
                          f"at {d.timestamp} — {d.rationale}"]
        lines.append("")
    usage = result.token_usage
    lines += [
        "---",
        f"LLM usage: calls={usage.calls} input_tokens={usage.input_tokens} "
        f"output_tokens={usage.output_tokens} "
        f"est_cost_usd={usage.estimated_cost_usd} retries={usage.retries} "
        f"errors={usage.errors}",
        "",
        "Spectra is decision support. It does not bind, decline, change premiums, "
        "or make legal coverage determinations.",
    ]
    return "\n".join(lines)


def _source_label(evidence) -> str:
    if evidence.source_file:
        return Path(evidence.source_file).name
    name = evidence.document_name or evidence.document_id or "Source document"
    if "<inline:" in name or Path(name.split(" :: ", 1)[0]).suffix.lower() == ".json":
        return "Source document"
    return name


def _source_label_from_name(name, document_id) -> str:
    label = name or document_id or "Source document"
    return "Source document" if ("<inline:" in label
                                  or Path(label.split(" :: ", 1)[0]).suffix.lower() == ".json") else label


def _citation_location(evidence) -> str:
    loc = evidence.source_location
    parts = []
    for label, value in (("page", loc.page), ("sheet", loc.sheet),
                         ("row", loc.row), ("cell", loc.cell),
                         ("column", loc.column), ("record", loc.record_id)):
        if value is not None:
            parts.append(f"{label}={value}")
    if loc.url:
        parts.append(f"url={loc.url}")
    return ", ".join(parts) if parts else "source location unavailable"


def _clean_inline_ids(value: str) -> str:
    import re
    return re.sub(r"GEN-<inline:\d+>", "the other submission document",
                  re.sub(r"<inline:\d+>", "submission document", value or ""))


def _finding_text(value: str, evidence) -> str:
    text = value or ""
    for item in evidence:
        if item.document_id:
            text = text.replace(item.document_id, _source_label(item))
    return _clean_inline_ids(text)
