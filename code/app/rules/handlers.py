"""Deterministic rule handlers for the Spectra issue taxonomy.

Each handler is a pure function of (RuleContext) -> list[FindingDraft].
Handlers never mutate the case, never invent provenance, and treat
unresolved entity/period alignment conservatively (qualified findings,
not confirmed conflicts).
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from app.entity_resolution import align_periods, match_locations, match_names
from app.findings.builder import FindingDraft
from app.normalization import normalize_address, normalize_class_code, normalize_date, normalize_state
from app.rules.engine import RuleContext
from app.schemas.canonical import CanonicalCase, EvidenceItem, LocationRecord, WCPayrollRow
from app.schemas.findings import (
    CalculationDetail,
    CalculationInput,
    ComparisonSource,
    ConclusionState,
    FindingClassification,
    MaterialityBand,
    Severity,
)

DOC_FAMILIES = {"acord_125", "acord_126", "acord_130", "acord_140", "sov"}


def _ev(record, *fields: str) -> List[str]:
    return [record.field_evidence[f] for f in fields if f in record.field_evidence]


def _draft(ctx: RuleContext, **kwargs) -> FindingDraft:
    kwargs.setdefault("taxonomy_id", ctx.rule.taxonomy_id)
    kwargs.setdefault("severity", ctx.rule.severity)
    kwargs.setdefault("blocking", ctx.rule.blocking)
    return FindingDraft(
        rule_id=ctx.rule.rule_id, rule_version=ctx.rule.version, **kwargs)


def _doc_label(case: CanonicalCase, document_id: str) -> str:
    document = next((d for d in case.documents if d.document_id == document_id), None)
    return (document.document_name or document.document_type or document_id) if document else document_id


def _document_table_evidence(case: CanonicalCase, document_id: str) -> List[str]:
    """Return source-backed table chunks that show the document's schedule."""
    return [item.evidence_id for item in case.evidence_store.values()
            if item.document_id == document_id
            and item.field_path.startswith("worksheet[")
            and item.field_path.endswith(".table_rows")]


def _today() -> date:
    return datetime.now().date()


# ---------------------------------------------------------------------
# Stage 1: document inventory / completeness
# ---------------------------------------------------------------------

def doc_inventory(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    profile = ctx.profile
    if profile is None:
        return drafts
    required = profile.required_types(case.line_of_business)
    present = {d.document_type: d for d in case.documents}
    for doc_type, req in required.items():
        if doc_type not in present:
            drafts.append(_draft(
                ctx, finding_type="MISSING_REQUIRED_DOCUMENT",
                title=f"Required document missing: {doc_type}",
                classification=FindingClassification.EVIDENCE_GAP,
                conclusion_state=ConclusionState.EVIDENCE_GAP,
                materiality_band=MaterialityBand.MEDIUM,
                materiality_rationale=req.rationale or "Required by configured profile",
                evidence_ids=[],
                uncertainty_reason="Document absent from submission; absence is an evidence "
                                   "gap, not proof of an adverse fact",
                possible_consequence="Review cannot confirm the checks that depend on this document",
                recommended_action=f"Request the {doc_type} from the broker or mark the "
                                   "requirement not applicable with rationale",
                deduplication_key=f"DOC-MISSING:{doc_type}"))
    for doc in case.documents:
        if not doc.recognized:
            drafts.append(_draft(
                ctx, finding_type="UNSUPPORTED_DOCUMENT_TYPE",
                title=f"Unsupported/unmapped document: {doc.document_name or doc.document_id}",
                classification=FindingClassification.EVIDENCE_GAP,
                conclusion_state=ConclusionState.EVIDENCE_GAP,
                severity=Severity.LOW,
                materiality_band=MaterialityBand.UNKNOWN,
                materiality_rationale="Unknown document types are preserved but not analyzed",
                evidence_ids=[],
                uncertainty_reason="No adapter registered for this document type",
                possible_consequence="Content of this document was not included in checks",
                recommended_action="Register an adapter or confirm the document is informational",
                deduplication_key=f"DOC-UNSUPPORTED:{doc.document_id}"))
        elif not doc.raw:
            drafts.append(_draft(
                ctx, finding_type="EMPTY_DOCUMENT",
                title=f"Document present but empty: {doc.document_name or doc.document_id}",
                classification=FindingClassification.EVIDENCE_GAP,
                conclusion_state=ConclusionState.EVIDENCE_GAP,
                severity=Severity.LOW,
                evidence_ids=[],
                possible_consequence="No data available from this document",
                recommended_action="Obtain a populated version of the document",
                deduplication_key=f"DOC-EMPTY:{doc.document_id}"))
    # exact-duplicate detection via content fingerprint
    by_hash: Dict[str, list] = defaultdict(list)
    for doc in case.documents:
        if doc.content_hash:
            by_hash[doc.content_hash].append(doc)
    for docs in by_hash.values():
        if len(docs) > 1:
            ids = ", ".join(d.document_id for d in docs)
            drafts.append(_draft(
                ctx, finding_type="DUPLICATE_DOCUMENT",
                title=f"Duplicate document content: {ids}",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                severity=Severity.LOW,
                evidence_ids=[],
                possible_consequence="Duplicate documents can distort counts and version review",
                recommended_action="Confirm which document instance is authoritative",
                deduplication_key=f"DOC-DUP:{docs[0].content_hash}"))
    return drafts


# ---------------------------------------------------------------------
# P1 - driver roster / MVR evidence
# ---------------------------------------------------------------------

def p1_driver_roster(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    if case.has_document_type("driver_roster"):
        return []
    evidence: List[str] = []
    notes: List[str] = []
    for item in case.checklist_items:
        name = item.item.lower()
        if ("driver" in name or "mvr" in name) and item.status and item.status.lower() in {
            "missing", "not_attached", "not attached", "false", "no"
        }:
            evidence.extend(_ev(item, "status"))
            notes.append(f"'{item.item}' marked {item.status}")
    if case.has_document_type("fleet_schedule"):
        refs = [v.field_evidence["assigned_driver_id"] for v in case.vehicles
                if "assigned_driver_id" in v.field_evidence][:3]
        if refs:
            evidence.extend(refs)
            notes.append("fleet schedule references assigned drivers")
    if not evidence and not case.has_document_type("fleet_schedule"):
        return []  # profile handles generic missing-doc; nothing auto-specific to say
    return [_draft(
        ctx, finding_type="MISSING_DRIVER_ROSTER", taxonomy_id="P1",
        title="No current driver roster / MVR review evidence in submission",
        classification=FindingClassification.EVIDENCE_GAP,
        conclusion_state=ConclusionState.EVIDENCE_GAP,
        materiality_band=MaterialityBand.HIGH,
        materiality_rationale="Driver qualification cannot be reviewed without a current roster",
        evidence_ids=evidence,
        uncertainty_reason="Missing evidence is not proof that any driver is unqualified",
        possible_consequence="Driver eligibility, MVR review status, and heavy-vehicle "
                             "qualification cannot be verified" + ("; " + "; ".join(notes) if notes else ""),
        recommended_action="Request the current driver roster and MVR review evidence; "
                           "do not infer driver disqualification from absence",
        deduplication_key="P1:driver_roster")]


# ---------------------------------------------------------------------
# P2 - location count / property value conflicts
# ---------------------------------------------------------------------

def _locations_by_doc(case: CanonicalCase) -> Dict[str, List[LocationRecord]]:
    grouped: Dict[str, List[LocationRecord]] = defaultdict(list)
    for loc in case.locations:
        grouped[loc.document_id].append(loc)
    return grouped


def _location_count_key(location: LocationRecord) -> str:
    """Build a stable, account-neutral key for counting scheduled premises.

    ACORD-style sources may represent the same premises using identifiers such
    as ``A02-LOC-01`` and ``01`` in separate parts of a form. When an identifier
    is explicitly location-like (or only numeric), compare its numeric suffix
    without leading zeroes. Preserve unrelated opaque identifiers verbatim.
    Address is a last resort when no identifier was extracted.
    """
    raw_id = str(location.location_id or "").strip().casefold()
    if raw_id:
        compact = re.sub(r"\s+", " ", raw_id)
        trailing_number = re.search(r"(\d+)\s*$", compact)
        is_location_label = bool(re.search(
            r"(?:^|[^a-z])(?:loc(?:ation)?|prem(?:ise|ises)?|site|building|bldg)(?:[^a-z]|$)",
            compact))
        if trailing_number and (compact.isdigit() or is_location_label):
            return f"number:{int(trailing_number.group(1))}"
        return f"id:{compact}"
    address = normalize_address(location.address)
    if address:
        return f"address:{address}"
    # Keep records with no usable identifier distinct rather than silently
    # collapsing unknown premises into a single count.
    return f"record:{location.record_id}"


def _distinct_location_records(locations: List[LocationRecord]) -> List[LocationRecord]:
    """Return one representative per logical location in a single schedule."""
    unique: Dict[str, LocationRecord] = {}
    for location in locations:
        unique.setdefault(_location_count_key(location), location)
    return list(unique.values())


def _location_count_evidence(case: CanonicalCase,
                             locations: List[LocationRecord]) -> List[str]:
    """Cite every source-backed location used to support a schedule count."""
    evidence: List[str] = []
    for location in _distinct_location_records(locations):
        refs = _ev(location, "location_id", "address", "state")
        refs = [eid for eid in refs if eid in case.evidence_store]
        if not refs:
            return []
        evidence.extend(refs[:1])
    return evidence


def _addresses_compatible(left: Optional[str], right: Optional[str]) -> bool:
    """Treat a street-only address as compatible with the same full address.

    Missing city/state/ZIP detail is an evidence-completeness gap, not itself
    proof that two scheduled locations conflict. Any contradictory supplied
    address tokens still make the pair conflict.
    """
    if not left or not right:
        return True
    def tokens(value: str) -> set[str]:
        normalized = normalize_address(value) or ""
        return {normalize_state(token) or token for token in normalized.split()}
    left_parts = [part.strip() for part in left.split(",") if part.strip()]
    right_parts = [part.strip() for part in right.split(",") if part.strip()]
    if not left_parts or not right_parts:
        return tokens(left).issubset(tokens(right)) or tokens(right).issubset(tokens(left))
    left_street, right_street = tokens(left_parts[0]), tokens(right_parts[0])
    if left_street != right_street:
        return False
    left_tail = set().union(*(tokens(part) for part in left_parts[1:])) if len(left_parts) > 1 else set()
    right_tail = set().union(*(tokens(part) for part in right_parts[1:])) if len(right_parts) > 1 else set()
    return left_tail.issubset(right_tail) or right_tail.issubset(left_tail)


def p2_location_reconciliation(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    docs = [d for d in case.documents if d.document_type in DOC_FAMILIES]
    if len(docs) < 2:
        return drafts
    grouped = _locations_by_doc(case)
    if len(grouped) < 2:
        return drafts

    # A location number repeated across source documents can refer to a
    # corresponding schedule row, but a different street address is the
    # conflict itself. Report the two source values side by side rather than
    # claiming the premise is simply present in both documents.
    by_location_id: Dict[str, List[LocationRecord]] = defaultdict(list)
    for doc_id, locs in grouped.items():
        for loc in locs:
            if loc.location_id and loc.address:
                by_location_id[loc.location_id].append(loc)
    conflicting_location_ids = set()
    for location_id, members in by_location_id.items():
        by_doc: Dict[str, List[LocationRecord]] = defaultdict(list)
        for member in members:
            by_doc[member.document_id].append(member)
        if len(by_doc) < 2:
            continue
        address_conflict = any(
            not _addresses_compatible(left.address, right.address)
            for left_doc, left_members in by_doc.items()
            for right_doc, right_members in by_doc.items()
            if left_doc < right_doc
            for left in left_members for right in right_members
        )
        if not address_conflict:
            continue
        conflicting_location_ids.add(location_id)
        source_comparisons = []
        evidence_ids = []
        for doc_id, doc_members in by_doc.items():
            member_evidence = [eid for member in doc_members
                               for eid in _ev(member, "address")]
            member_evidence = [eid for eid in dict.fromkeys(member_evidence)
                               if eid in case.evidence_store]
            evidence_ids.extend(member_evidence)
            source_comparisons.append(ComparisonSource(
                document_id=doc_id,
                document_name=_doc_label(case, doc_id),
                label="Conflicting scheduled address",
                value="; ".join(member.address or "" for member in doc_members),
                status="CONFLICTING",
                evidence_ids=member_evidence,
            ))
        if not evidence_ids:
            continue
        address_summary = " vs ".join(
            f"{_doc_label(case, doc_id)}: {', '.join(member.address or '' for member in doc_members)}"
            for doc_id, member in by_doc.items()
        )
        drafts.append(_draft(
            ctx, finding_type="LOCATION_SET_CONFLICT",
            title=f"Location ID {location_id} has conflicting addresses across documents",
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
            materiality_band=MaterialityBand.HIGH,
            materiality_rationale="Conflicting addresses prevent reliable premises identification",
            evidence_ids=evidence_ids,
            comparison=ctx.comparison(
                [source.value for source in source_comparisons],
                entity_alignment="PARTIAL", period_alignment="UNRESOLVED").model_copy(
                    update={"sources": source_comparisons}),
            possible_consequence=f"Location ID {location_id} has different scheduled addresses: {address_summary}",
            recommended_action="Confirm which address corresponds to this location ID in each schedule",
            deduplication_key=f"P2:location-address:{location_id}"))

    # Cluster locations across documents by conservative matching.
    clusters: List[dict] = []
    for doc_id, locs in grouped.items():
        for loc in locs:
            if loc.location_id in conflicting_location_ids:
                continue
            placed = False
            for cluster in clusters:
                if match_locations(cluster["rep"], loc).state == "MATCHED":
                    cluster["members"].append(loc)
                    placed = True
                    break
            if not placed:
                clusters.append({"rep": loc, "members": [loc]})

    doc_sets = {doc_id: set() for doc_id in grouped}
    for idx, cluster in enumerate(clusters):
        for member in cluster["members"]:
            doc_sets[member.document_id].add(idx)

    # One finding per premises whose coverage differs across documents —
    # the register asks for distinct discrepancy instances, not one alert
    # per document pair repeating the same missing location.
    all_docs = set(grouped)
    for idx, cluster in enumerate(clusters):
        present_in = {m.document_id for m in cluster["members"]}
        missing_from = sorted(all_docs - present_in)
        if not missing_from:
            continue
        rep = cluster["rep"]
        evidence: List[str] = []
        by_doc: Dict[str, List[LocationRecord]] = defaultdict(list)
        for member in cluster["members"]:
            by_doc[member.document_id].append(member)
        source_comparisons: List[ComparisonSource] = []
        for doc_id, members in by_doc.items():
            member_evidence = [eid for member in members
                               for eid in _ev(member, "address", "state")]
            evidence.extend(member_evidence)
            source_comparisons.append(ComparisonSource(
                document_id=doc_id, document_name=_doc_label(case, doc_id),
                label="Premises found", value="Present", status="PRESENT",
                evidence_ids=member_evidence))
        for doc_id in missing_from:
            schedule_evidence = _document_table_evidence(case, doc_id)
            evidence.extend(schedule_evidence)
            source_comparisons.append(ComparisonSource(
                document_id=doc_id, document_name=_doc_label(case, doc_id),
                label="No matching premises row", value="Not listed", status="NOT_LISTED",
                evidence_ids=schedule_evidence))
        label = rep.address or rep.location_id or f"cluster {idx}"
        present_names = ", ".join(_doc_label(case, doc_id) for doc_id in sorted(present_in))
        missing_names = ", ".join(_doc_label(case, doc_id) for doc_id in missing_from)
        drafts.append(_draft(
            ctx, finding_type="LOCATION_SET_CONFLICT",
            title=f"Premises {label}: listed in {present_names}; not listed in {missing_names}",
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
            materiality_band=MaterialityBand.HIGH,
            materiality_rationale="Premises schedule disagreement affects property/WC/GL scoping",
            evidence_ids=evidence,
            comparison=ctx.comparison(
                [len(present_in), len(all_docs)],
                entity_alignment="PARTIAL",
                period_alignment="UNRESOLVED").model_copy(
                    update={"sources": source_comparisons}),
            possible_consequence=f"{label} appears in {present_names} but no matching premises "
                                 f"row was found in {missing_names}.",
            recommended_action="Confirm the authoritative premises schedule with the insured; "
                               "verify location IDs before treating counts as final",
            deduplication_key=f"P2:set:{rep.location_id or normalize_address(rep.address or '') or idx}"))

    # Declared vs scheduled location counts (per document pair totals are
    # implied by the per-premises findings above; report the count delta once
    # against the largest schedule as reference).
    counts = {doc_id: len(_distinct_location_records(locs))
              for doc_id, locs in grouped.items()}
    distinct_counts = set(counts.values())
    if len(distinct_counts) > 1:
        ref_doc = max(counts, key=lambda d: counts[d])
        ref_count = counts[ref_doc]
        for doc_id, count in sorted(counts.items()):
            if doc_id == ref_doc or count == ref_count:
                continue
            compared_evidence = _location_count_evidence(case, grouped[doc_id])
            reference_evidence = _location_count_evidence(case, grouped[ref_doc])
            if not compared_evidence or not reference_evidence:
                continue
            evidence = compared_evidence + reference_evidence
            comparison = ctx.comparison(
                [count, ref_count], delta=count - ref_count, base=ref_count,
                formula=f"{count} scheduled locations - {ref_count} scheduled locations",
                entity_alignment="PARTIAL", period_alignment="UNRESOLVED")
            comparison.sources = [
                ComparisonSource(document_id=doc_id, document_name=_doc_label(case, doc_id),
                                 label="Scheduled location count", value=count,
                                 evidence_ids=compared_evidence),
                ComparisonSource(document_id=ref_doc, document_name=_doc_label(case, ref_doc),
                                 label="Scheduled location count", value=ref_count,
                                 evidence_ids=reference_evidence),
            ]
            drafts.append(_draft(
                ctx, finding_type="LOCATION_COUNT_CONFLICT",
                title=f"Location count differs: {_doc_label(case, doc_id)} has {count}; "
                      f"{_doc_label(case, ref_doc)} has {ref_count}",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                materiality_band=MaterialityBand.HIGH,
                materiality_rationale="Location count is a core premises scope input",
                evidence_ids=evidence,
                comparison=comparison,
                possible_consequence="Premises scope differs between documents",
                recommended_action="Confirm the authoritative location count with the insured",
                deduplication_key=f"P2:count:{doc_id}:{ref_doc}"))

    # Compare like with like at each matched premises. A building-only ACORD
    # value must not be compared with a schedule's total (building plus
    # contents/other property); only compare TIV when each document supplies
    # an explicit total or both building and contents components.
    for idx, cluster in enumerate(clusters):
        by_doc: Dict[str, LocationRecord] = {}
        for member in cluster["members"]:
            by_doc.setdefault(member.document_id, member)
        value_fields = (
            ("building_value", "building value"),
            ("contents_value", "contents value"),
        )
        comparable_totals = {
            doc_id: member.tiv for doc_id, member in by_doc.items()
            if member.tiv is not None and (
                "tiv" in member.field_evidence
                or (member.building_value is not None and member.contents_value is not None))
        }
        if len(comparable_totals) > 1:
            value_fields += (("tiv", "total insured value"),)
        rep = cluster["rep"]
        for field_name, label in value_fields:
            values = {doc_id: getattr(member, field_name)
                      for doc_id, member in by_doc.items()
                      if getattr(member, field_name) is not None}
            if field_name == "tiv":
                values = comparable_totals
            if len(values) < 2:
                continue
            vals = list(values.items())
            base_val = min(value for _, value in vals)
            max_val = max(value for _, value in vals)
            delta = max_val - base_val
            if ctx.within_tolerance(delta, base_val):
                continue
            evidence: List[str] = []
            for doc_id, _ in vals:
                member = by_doc[doc_id]
                evidence.extend(_ev(member, field_name))
            detail = "; ".join(f"{doc_id}: {value:,.0f}" for doc_id, value in vals)
            drafts.append(_draft(
                ctx, finding_type="PROPERTY_VALUE_CONFLICT",
                title=f"Property {label} conflict at {rep.address or rep.location_id}",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                evidence_ids=evidence,
                comparison=ctx.comparison(
                    [value for _, value in vals], delta=delta, base=base_val,
                    formula=f"max {max_val} - min {base_val} for {label}",
                    entity_alignment="MATCHED", period_alignment="UNRESOLVED"),
                possible_consequence=f"Like-for-like {label} values differ between documents: {detail}",
                recommended_action="Request a corrected schedule of values; confirm valuation basis and date",
                deduplication_key=f"P2:val:{field_name}:{rep.location_id or rep.address}"))
    return drafts


# ---------------------------------------------------------------------
# P3 - declared exposure mismatches
# ---------------------------------------------------------------------

def p3_fleet_count(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    vehicles = [v for v in case.vehicles if v.document_id and case.has_document_type("fleet_schedule")]
    fleet_doc_ids = {d.document_id for d in case.documents_of_type("fleet_schedule")}
    vehicles = [v for v in case.vehicles if v.document_id in fleet_doc_ids]
    active = {v.record_id: v for v in vehicles if (v.status or "active") in {"active", "in_service"}}
    declared: Optional[float] = None
    declared_ev: List[str] = []
    for exp in case.exposures:
        if exp.exposure_type == "vehicle_count" and exp.value is not None:
            declared = float(exp.value)
            declared_ev = _ev(exp, "value")
    if declared is None or not case.has_document_type("fleet_schedule"):
        return []
    actual = len(active)
    delta = declared - actual
    if ctx.within_tolerance(delta, actual):
        return []
    evidence = declared_ev[:]
    for v in list(active.values())[:5]:
        evidence.extend(_ev(v, "status", "vin"))
    return [_draft(
        ctx, finding_type="DECLARED_EXPOSURE_MISMATCH", taxonomy_id="P3-FLEET-COUNT",
        title=f"Declared power units ({int(declared)}) vs active fleet schedule units ({actual})",
        classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
        conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
        materiality_band=MaterialityBand.HIGH,
        materiality_rationale="Vehicle count is a pricing-sensitive rating input",
        evidence_ids=evidence,
        comparison=ctx.comparison(
            [declared, actual], delta=delta, base=actual,
            formula=f"declared {declared} - unique active units {actual}",
            entity_alignment="MATCHED", period_alignment="PARTIAL"),
        possible_consequence="Quote may be rated on a different unit count than the eligible fleet",
        recommended_action="Reconcile declared power units against the fleet schedule; confirm "
                           "inclusion criteria and reporting dates before adjusting rating inputs",
        deduplication_key="P3-FLEET-COUNT")]


def p3_wc_payroll(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    sums: Dict[str, Tuple[float, List[str]]] = {}
    for doc_type in ("acord_130", "wc_rating_workbook", "quote"):
        doc_ids = {d.document_id for d in case.documents_of_type(doc_type)}
        rows = [r for r in case.wc_payroll_rows if r.document_id in doc_ids and r.payroll is not None]
        if rows:
            total = sum(r.payroll or 0 for r in rows)
            evidence: List[str] = []
            for r in rows:
                evidence.extend(_ev(r, "payroll"))
            sums[doc_type] = (total, evidence)
    declared_total: Optional[float] = None
    declared_ev: List[str] = []
    for exp in case.exposures:
        if exp.exposure_type == "declared_payroll_total" and exp.value is not None:
            declared_total = float(exp.value)
            declared_ev = _ev(exp, "value")
    if declared_total is not None and "wc_rating_workbook" in sums:
        detail_sum, detail_ev = sums["wc_rating_workbook"]
        delta = declared_total - detail_sum
        if not ctx.within_tolerance(delta, detail_sum):
            comparison = ctx.comparison(
                [declared_total, detail_sum], delta=delta, base=detail_sum,
                formula=f"declared total {declared_total:,.0f} - sum of class rows {detail_sum:,.0f}",
                entity_alignment="MATCHED", period_alignment="MATCHED")
            declared_doc_id = next((item.document_id for item in case.evidence_store.values()
                                    if item.evidence_id in declared_ev), "declared-total")
            detail_doc_id = next((item.document_id for item in case.evidence_store.values()
                                  if item.evidence_id in detail_ev), "workbook-rows")
            comparison.sources = [
                ComparisonSource(document_id=declared_doc_id,
                                 document_name=_doc_label(case, declared_doc_id),
                                 label="Declared WC payroll", value=declared_total,
                                 evidence_ids=declared_ev),
                ComparisonSource(document_id=detail_doc_id,
                                 document_name=_doc_label(case, detail_doc_id),
                                 label="Sum of WC class rows", value=detail_sum,
                                 evidence_ids=detail_ev[:10]),
            ]
            drafts.append(_draft(
                ctx, finding_type="DECLARED_EXPOSURE_MISMATCH", taxonomy_id="P3-WC-PAYROLL",
                title="WC workbook declared total payroll does not equal sum of class/location rows",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                materiality_band=MaterialityBand.HIGH,
                materiality_rationale="Payroll is the WC exposure base",
                evidence_ids=declared_ev + detail_ev[:10],
                comparison=comparison,
                possible_consequence="Rating input payroll is internally inconsistent",
                recommended_action="Obtain a corrected workbook; do not alter rating inputs in Spectra",
                deduplication_key="P3-WC-PAYROLL:workbook-internal"))
    keys = sorted(sums)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            total_a, ev_a = sums[a]
            total_b, ev_b = sums[b]
            delta = total_a - total_b
            if ctx.within_tolerance(delta, total_b):
                continue
            evidence_a = ev_a[:5]
            evidence_b = ev_b[:5]
            doc_ids_a = list(dict.fromkeys(case.evidence_store[eid].document_id
                                           for eid in evidence_a if eid in case.evidence_store))
            doc_ids_b = list(dict.fromkeys(case.evidence_store[eid].document_id
                                           for eid in evidence_b if eid in case.evidence_store))
            label_a = ", ".join(_doc_label(case, doc_id) for doc_id in doc_ids_a) or a
            label_b = ", ".join(_doc_label(case, doc_id) for doc_id in doc_ids_b) or b
            comparison = ctx.comparison(
                [total_a, total_b], delta=delta, base=total_b,
                formula=f"{total_a:,.0f} - {total_b:,.0f} = {delta:,.0f}",
                entity_alignment="MATCHED", period_alignment="PARTIAL")
            comparison.sources = [
                ComparisonSource(document_id=doc_ids_a[0] if doc_ids_a else a,
                                 document_name=label_a,
                                 label=f"{a.replace('_', ' ').upper()} payroll total",
                                 value=total_a, evidence_ids=evidence_a),
                ComparisonSource(document_id=doc_ids_b[0] if doc_ids_b else b,
                                 document_name=label_b,
                                 label=f"{b.replace('_', ' ').upper()} payroll total",
                                 value=total_b, evidence_ids=evidence_b),
            ]
            drafts.append(_draft(
                ctx, finding_type="DECLARED_EXPOSURE_MISMATCH", taxonomy_id="P3-WC-PAYROLL",
                title=f"WC payroll differs: {label_a} vs {label_b}",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                materiality_band=MaterialityBand.HIGH,
                materiality_rationale="Payroll is the WC exposure base",
                evidence_ids=evidence_a + evidence_b,
                comparison=comparison,
                uncertainty_reason="Periods/inclusion criteria assumed comparable; confirm before treating as final",
                possible_consequence="Quote may use payroll inconsistent with the application",
                recommended_action="Reconcile payroll by class and location across the application, "
                                   "workbook, and quote; verify periods align",
                deduplication_key=f"P3-WC-PAYROLL:{a}:{b}"))
    return drafts


# ---------------------------------------------------------------------
# P4 - unexplained document-version discrepancy
# ---------------------------------------------------------------------

def p4_version_discrepancy(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    groups: Dict[Tuple[str, str], list] = defaultdict(list)
    for doc in case.documents:
        base_name = re.sub(r"[#]\d+$", "", doc.document_id)
        key = (doc.document_type, (doc.document_name or base_name).lower().split("#")[0])
        groups[key].append(doc)
    for (doc_type, _), docs in groups.items():
        distinct = {d.content_hash for d in docs if d.content_hash}
        versions = {d.version for d in docs if d.version}
        if len(docs) < 2 or len(distinct) < 2:
            continue
        has_change_log = any(
            (d.raw.get("source_metadata", {}) or {}).get("change_log")
            or (d.raw.get("change_log"))
            for d in docs
        )
        if has_change_log:
            continue  # legitimate, explained version change
        document_ids = {doc.document_id for doc in docs}
        by_field: Dict[str, Dict[str, EvidenceItem]] = defaultdict(dict)
        for item in case.evidence_store.values():
            if item.document_id in document_ids:
                by_field[item.field_path][item.document_id] = item
        evidence = [
            item.evidence_id for item in case.evidence_store.values()
            if item.document_id in document_ids
            and item.field_path == "$document.content_hash"
        ]
        for field_path, field_items in by_field.items():
            if field_path == "$document.content_hash":
                continue
            if len(field_items) < 2:
                continue
            values = {
                (str(item.raw_value), str(item.normalized_value))
                for item in field_items.values()
            }
            if len(values) > 1:
                evidence.extend(item.evidence_id for item in field_items.values())
        if not evidence:
            continue
        drafts.append(_draft(
            ctx, finding_type="UNEXPLAINED_VERSION_DIFFERENCE", taxonomy_id="P4",
            title=f"Unexplained material difference between {doc_type} versions "
                  f"({', '.join(sorted(versions)) if versions else 'unversioned'})",
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=ConclusionState.POSSIBLE_MISMATCH,
            materiality_band=MaterialityBand.MEDIUM,
            materiality_rationale="Resubmitted data changed without a documented reason",
            evidence_ids=evidence,
            uncertainty_reason="A version difference is a review indicator, not evidence of manipulation",
            possible_consequence="Review may be based on superseded values; changed fields need confirmation",
            recommended_action="Request the change log or reason for resubmission; identify which "
                               "version is authoritative",
            deduplication_key=f"P4:{doc_type}"))
    return drafts


# ---------------------------------------------------------------------
# P5 - claims / loss-history reconciliation
# ---------------------------------------------------------------------

def _claim_reconciliation_evidence(claim, *fields: str) -> List[str]:
    """Resolve canonical claim financial fields back to extracted cell evidence."""
    aliases = {
        "paid": ("paid", "paid_indemnity", "paid_expense"),
        "reserves": ("reserves", "reserve", "outstanding",
                     "reserve_indemnity", "reserve_expense"),
        "incurred": ("incurred", "total_incurred"),
        "calculated_incurred": ("calculated_incurred",),
        "incurred_variance": ("incurred_variance",),
        "claim_id": ("claim_id",),
        "status": ("status",),
    }
    evidence: List[str] = []
    for field in fields:
        for key in aliases.get(field, (field,)):
            evidence.extend(_ev(claim, key))
    return list(dict.fromkeys(evidence))


def p5_claims_reconciliation(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    loss_doc_ids = {d.document_id for d in case.documents_of_type("loss_run")}
    claims = [c for c in case.claims if c.document_id in loss_doc_ids]
    if not claims:
        return drafts

    # (a) duplicate claim IDs with conflicting data
    by_claim: Dict[str, list] = defaultdict(list)
    for c in claims:
        if c.claim_id:
            by_claim[c.claim_id].append(c)
    for claim_id, rows in by_claim.items():
        if len(rows) < 2:
            continue
        signatures = {(r.paid, r.reserves, r.incurred_reported, r.status) for r in rows}
        if len(signatures) > 1:
            evidence: List[str] = []
            for r in rows:
                evidence.extend(_claim_reconciliation_evidence(
                    r, "claim_id", "paid", "reserves", "incurred", "status"))
            drafts.append(_draft(
                ctx, finding_type="DUPLICATE_CLAIM_CONFLICT", taxonomy_id="P5",
                title=f"Claim {claim_id} appears {len(rows)} times with conflicting values",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                materiality_band=MaterialityBand.HIGH,
                materiality_rationale="Duplicate/conflicting loss records distort loss history",
                evidence_ids=evidence,
                possible_consequence="Loss history and experience view may be overstated or double-counted",
                recommended_action="Confirm the correct claim record with the carrier/TPA and obtain "
                                   "a corrected loss run",
                deduplication_key=f"P5:dup:{claim_id}"))

    # (b) incurred vs paid + reserves per claim (definitions supplied in shape)
    for c in claims:
        if c.paid is None or c.reserves is None or c.incurred_reported is None:
            continue
        expected = c.paid + c.reserves
        delta = c.incurred_reported - expected
        if ctx.within_tolerance(delta, expected):
            continue
        def component(field: str, label: str):
            evidence_id = c.field_evidence.get(field)
            item = case.evidence_store.get(evidence_id) if evidence_id else None
            if item is None:
                return None
            value = (item.normalized_value if item.normalized_value is not None
                     else item.raw_value)
            if not isinstance(value, (int, float)):
                return None
            return CalculationInput(label=label, value=value,
                                    evidence_ids=[evidence_id])

        paid_parts = [item for item in (
            component("paid_indemnity", "Paid indemnity"),
            component("paid_expense", "Paid expense"),
        ) if item is not None]
        reserve_parts = [item for item in (
            component("reserve_indemnity", "Reserve indemnity"),
            component("reserve_expense", "Reserve expense"),
        ) if item is not None]
        paid_evidence = list(dict.fromkeys(
            eid for item in paid_parts for eid in item.evidence_ids
        )) or _claim_reconciliation_evidence(c, "paid")
        reserve_evidence = list(dict.fromkeys(
            eid for item in reserve_parts for eid in item.evidence_ids
        )) or _claim_reconciliation_evidence(c, "reserves")
        reported_evidence = _claim_reconciliation_evidence(c, "incurred")
        calculated_cell_evidence = _claim_reconciliation_evidence(
            c, "calculated_incurred")
        variance_cell_evidence = _claim_reconciliation_evidence(
            c, "incurred_variance")
        calculated_evidence = list(dict.fromkeys(
            paid_evidence + reserve_evidence + calculated_cell_evidence))
        inputs = list(paid_parts)
        inputs.append(CalculationInput(
            label="Total paid", value=c.paid, evidence_ids=paid_evidence))
        inputs.extend(reserve_parts)
        inputs.extend([
            CalculationInput(label="Total reserves", value=c.reserves,
                             evidence_ids=reserve_evidence),
            CalculationInput(label="Calculated incurred · paid + reserves",
                             value=expected, evidence_ids=calculated_evidence),
            CalculationInput(label="Reported incurred", value=c.incurred_reported,
                             evidence_ids=reported_evidence),
            CalculationInput(label="Variance · reported − calculated",
                             value=delta,
                             evidence_ids=list(dict.fromkeys(
                                 reported_evidence + calculated_evidence
                                 + variance_cell_evidence)),
            ),
        ])
        if calculated_cell_evidence:
            calculated_item = case.evidence_store.get(calculated_cell_evidence[0])
            if calculated_item is not None:
                inputs.append(CalculationInput(
                    label="Workbook calculated incurred", value=(
                        calculated_item.normalized_value
                        if calculated_item.normalized_value is not None
                        else calculated_item.raw_value),
                    evidence_ids=calculated_cell_evidence))
        if variance_cell_evidence:
            variance_item = case.evidence_store.get(variance_cell_evidence[0])
            if variance_item is not None:
                inputs.append(CalculationInput(
                    label="Workbook variance", value=(
                        variance_item.normalized_value
                        if variance_item.normalized_value is not None
                        else variance_item.raw_value),
                    evidence_ids=variance_cell_evidence))
        comparison_sources = [
            ComparisonSource(
                document_id=c.document_id,
                document_name=_doc_label(case, c.document_id),
                label="Reported incurred", value=c.incurred_reported,
                status="CONFLICTING", evidence_ids=reported_evidence),
            ComparisonSource(
                document_id=c.document_id,
                document_name=_doc_label(case, c.document_id),
                label="Calculated incurred · paid + reserves", value=expected,
                status="CALCULATED", evidence_ids=calculated_evidence),
        ]
        if variance_cell_evidence:
            comparison_sources.append(ComparisonSource(
                document_id=c.document_id,
                document_name=_doc_label(case, c.document_id),
                label="Workbook-reported variance", value=delta,
                status="CONFLICTING", evidence_ids=variance_cell_evidence))
        paid_indemnity = next((item.value for item in paid_parts
                               if item.label == "Paid indemnity"), None)
        paid_expense = next((item.value for item in paid_parts
                             if item.label == "Paid expense"), None)
        reserve_indemnity = next((item.value for item in reserve_parts
                                  if item.label == "Reserve indemnity"), None)
        reserve_expense = next((item.value for item in reserve_parts
                                if item.label == "Reserve expense"), None)
        formula = ("Total paid = paid indemnity + paid expense; "
                   "total reserves = reserve indemnity + reserve expense; "
                   "calculated incurred = total paid + total reserves; "
                   "variance = reported incurred − calculated incurred.")
        rationale_parts = []
        if paid_indemnity is not None and paid_expense is not None:
            rationale_parts.append(
                f"Paid: {paid_indemnity:,.2f} + {paid_expense:,.2f} = {c.paid:,.2f}")
        else:
            rationale_parts.append(f"Total paid: {c.paid:,.2f}")
        if reserve_indemnity is not None and reserve_expense is not None:
            rationale_parts.append(
                f"Reserves: {reserve_indemnity:,.2f} + {reserve_expense:,.2f} = "
                f"{c.reserves:,.2f}")
        else:
            rationale_parts.append(f"Total reserves: {c.reserves:,.2f}")
        rationale_parts.append(
            f"Calculated incurred: {c.paid:,.2f} + {c.reserves:,.2f} = {expected:,.2f}")
        rationale_parts.append(
            f"Reported incurred: {c.incurred_reported:,.2f}; variance: "
            f"{c.incurred_reported:,.2f} − {expected:,.2f} = {delta:,.2f}")
        calculation = CalculationDetail(
            label="Claim incurred reconciliation", formula=formula,
            result=expected, rationale=". ".join(rationale_parts) + ".",
            inputs=inputs)
        drafts.append(_draft(
            ctx, finding_type="INCURRED_CALC_MISMATCH", taxonomy_id="P5",
            title=f"Claim {c.claim_id}: reported incurred does not equal paid + reserves",
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
            evidence_ids=_claim_reconciliation_evidence(
                c, "claim_id", "paid", "reserves", "incurred",
                "calculated_incurred", "incurred_variance"),
            comparison=ctx.comparison(
                [c.incurred_reported, expected], delta=delta, base=expected,
                formula=(f"Reported incurred {c.incurred_reported:,.2f} − "
                         f"(paid {c.paid:,.2f} + reserves {c.reserves:,.2f}) "
                         f"= {delta:,.2f}"),
                entity_alignment="MATCHED", period_alignment="MATCHED").model_copy(
                    update={"sources": comparison_sources,
                            "calculation": calculation}),
            uncertainty_reason=(
                f"On the cited loss-detail row, paid totals {c.paid:,.2f} and reserves "
                f"total {c.reserves:,.2f}, giving calculated incurred of {expected:,.2f}. "
                f"Reported incurred is {c.incurred_reported:,.2f}, a difference of "
                f"{delta:,.2f}. Confirm the financial basis with the loss-run source."),
            possible_consequence="Reported loss totals may be unreliable",
            recommended_action="Verify claim financials with the loss-run source system",
            deduplication_key=f"P5:calc:{c.claim_id}"))

    # (c) summary vs detail
    for exp in case.exposures:
        if exp.exposure_type != "loss_summary" or exp.document_id not in loss_doc_ids:
            continue
        detail_same_doc = [c for c in claims if c.document_id == exp.document_id]
        detail_incurred = [c.incurred_reported for c in detail_same_doc if c.incurred_reported is not None]
        if exp.value is not None and detail_incurred:
            detail_total = sum(detail_incurred)
            delta = float(exp.value) - detail_total
            if not ctx.within_tolerance(delta, detail_total):
                drafts.append(_draft(
                    ctx, finding_type="LOSS_SUMMARY_MISMATCH", taxonomy_id="P5",
                    title="Loss-run summary total incurred does not equal sum of detail records",
                    classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                    conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                    evidence_ids=_ev(exp, "total_incurred") + [
                        eid for c in detail_same_doc
                        for eid in _claim_reconciliation_evidence(c, "incurred")],
                    comparison=ctx.comparison(
                        [exp.value, detail_total], delta=delta, base=detail_total,
                        formula=f"summary {exp.value} - sum(detail) {detail_total}",
                        entity_alignment="MATCHED", period_alignment="MATCHED"),
                    possible_consequence="Aggregate loss figures used for review may be wrong",
                    recommended_action="Obtain a reconciled loss run; investigate duplicate or "
                                       "missing detail records",
                    deduplication_key=f"P5:summary:{exp.document_id}"))
        m = re.search(r"declared_count=(\d+|unknown)", exp.description or "")
        if m and m.group(1) != "unknown":
            declared_count = int(m.group(1))
            actual_count = len(detail_same_doc)
            if declared_count != actual_count:
                drafts.append(_draft(
                    ctx, finding_type="LOSS_COUNT_MISMATCH", taxonomy_id="P5",
                    title=f"Loss-run summary declares {declared_count} claims but detail contains {actual_count}",
                    classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                    conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                    evidence_ids=_ev(exp, "claim_count"),
                    comparison=ctx.comparison(
                        [declared_count, actual_count], delta=declared_count - actual_count,
                        base=actual_count, formula=f"declared {declared_count} - detail {actual_count}",
                        entity_alignment="MATCHED", period_alignment="MATCHED"),
                    possible_consequence="Claim frequency may be misstated",
                    recommended_action="Reconcile claim count with the detail listing",
                    deduplication_key=f"P5:count:{exp.document_id}"))
    return drafts


# ---------------------------------------------------------------------
# P6 - experience modification factor mismatch
# ---------------------------------------------------------------------

def p6_experience_mod(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    factors = [f for f in case.rating_factors
               if f.factor_type in {"experience_mod", "experience_mod_history"}
               and f.value is not None]
    eff = normalize_date(case.effective_date)
    def evidence_for(factor) -> List[str]:
        preferred = [factor.field_evidence.get("value"), factor.field_evidence.get("factor")]
        ordered = [eid for eid in preferred if eid in case.evidence_store]
        ordered.extend(eid for eid in factor.field_evidence.values()
                       if eid in case.evidence_store and eid not in ordered)
        return list(dict.fromkeys(ordered))

    def current_at_effective_date(factor) -> bool:
        date = normalize_date(factor.effective_date)
        return not eff or not date or date <= eff

    eligible_current = [f for f in factors
                        if f.factor_type == "experience_mod" and current_at_effective_date(f)]
    quote_current = [f for f in eligible_current
                     if (f.source_authority or "").casefold() == "quote"]
    support_current = [f for f in eligible_current if f not in quote_current]
    eligible_dates = [normalize_date(f.effective_date) for f in support_current
                      if normalize_date(f.effective_date)]
    comparison_date = max(eligible_dates) if eligible_dates else None
    current_support = [f for f in support_current
                       if not comparison_date or not normalize_date(f.effective_date)
                       or normalize_date(f.effective_date) == comparison_date]
    # The quote's policy effective date is not the effective date of its
    # underlying rating factor. Keep it in the comparison without allowing it
    # to displace a dated application/support value when choosing the current
    # experience period.
    current = current_support + quote_current
    if len({round(f.value, 4) for f in current}) < 2:
        return []

    # An as-of policy effective date calculation must prefer an identified
    # current bureau/approved worksheet value. It must never derive an
    # actuarial e-mod from a partial loss history.
    def is_authoritative_current_source(factor) -> bool:
        authority = (factor.source_authority or "").casefold()
        return (factor.factor_type == "experience_mod"
                and current_at_effective_date(factor)
                and any(token in authority for token in
                        ("bureau", "ncci", "wcirb", "approved", "rating_authority")))

    authoritative = [f for f in factors if is_authoritative_current_source(f)]
    selected = None
    calculation_issue = None
    if authoritative:
        dated = [(normalize_date(f.effective_date), f) for f in authoritative]
        dated = [(date, f) for date, f in dated if date]
        if dated:
            latest_date = max(date for date, _ in dated)
            latest = [f for date, f in dated if date == latest_date]
        else:
            latest = authoritative
        latest_values = {round(f.value, 4) for f in latest}
        if len(latest_values) == 1:
            selected = latest[0]
        else:
            calculation_issue = "Current authoritative sources disagree for the selected effective date."
    else:
        calculation_issue = "No current bureau or approved supporting factor is available for the policy effective date."

    observed = current
    methodology = next((obs for obs in case.external_observations
                        if str(obs.subject or "").casefold()
                        == "experience_rating_methodology"
                        and isinstance(obs.fields.get("formula"), str)
                        and obs.fields.get("formula")), None)
    methodology_evidence = list(methodology.field_evidence.values()) if methodology else []
    evidence: List[str] = []
    sources: List[ComparisonSource] = []
    calculation_inputs: List[CalculationInput] = []
    detail_lines: List[str] = []
    for f in factors:
        ids = evidence_for(f)
        evidence.extend(ids)
        is_history = f.factor_type == "experience_mod_history"
        is_future = f.factor_type == "experience_mod" and not current_at_effective_date(f)
        factor_date = normalize_date(f.effective_date)
        is_prior_term = is_history or bool(
            comparison_date and factor_date and factor_date < comparison_date)
        role = ("Future-term factor" if is_future else
                "Prior-term factor" if is_prior_term else
                "Draft-quote factor" if (f.source_authority or "").casefold() == "quote" else
                "Application/supporting factor")
        if f is selected:
            role = "Selected current factor"
        label = f"{role} · {f.source_authority or 'source not identified'} · effective {f.effective_date or 'date unknown'}"
        sources.append(ComparisonSource(
            document_id=f.document_id,
            document_name=_doc_label(case, f.document_id),
            label=role, value=f.value,
            status=("EXPECTED" if f is selected else
                    "FUTURE" if is_future else
                    "HISTORICAL" if is_prior_term else
                    "QUOTE" if (f.source_authority or "").casefold() == "quote" else "OBSERVED"),
            evidence_ids=ids))
        calculation_inputs.append(CalculationInput(
            label=label, value=f.value, evidence_ids=ids))
        detail_lines.append(
            f"{_doc_label(case, f.document_id)}: {f.value} ({role.lower()}, "
            f"effective {f.effective_date or 'unknown'}, source {f.source_authority or 'unknown'})")

    quote_values = {round(f.value, 4) for f in observed
                    if (f.source_authority or "").casefold() == "quote"}
    application_values = {round(f.value, 4) for f in observed
                          if (f.source_authority or "").casefold() != "quote"}
    mismatches_quote = bool(selected and quote_values
                            and round(selected.value, 4) not in quote_values)
    mismatches_other_current = bool(selected and application_values
                                    and round(selected.value, 4) not in application_values)
    provisional_values = {round(f.value, 4) for f in current_support}
    support_quote_mismatch = bool(provisional_values and quote_values
                                  and not (provisional_values & quote_values))
    prior_values = {round(f.value, 4) for f in factors
                    if f.factor_type == "experience_mod_history"}
    quote_matches_prior = bool(quote_values & prior_values)
    if selected and (mismatches_quote or mismatches_other_current):
        conclusion = ConclusionState.POSSIBLE_MISMATCH
        uncertainty = ("The source identifies a current effective-date factor, but its authority "
                       "and applicability must be confirmed before treating the difference as final.")
    else:
        conclusion = ConclusionState.POSSIBLE_MISMATCH
        uncertainty = calculation_issue or (
            "Observed current factor values differ, but the submission does not establish a single applicable value.")

    formula = (str(methodology.fields["formula"]) if methodology else
               "Experience modification = adjusted actual losses / adjusted expected losses "
               "under the applicable bureau plan; the jurisdiction-specific worksheet parameters "
               "are required to reproduce the factor.")
    if not eff:
        formula += " Policy effective date is not supplied, so term eligibility is unresolved."
    support_factor = (next(iter(provisional_values)) if len(provisional_values) == 1 else None)
    quote_factor = (next(iter(quote_values)) if len(quote_values) == 1 else None)
    factor_delta = (round(support_factor - quote_factor, 4)
                    if support_factor is not None and quote_factor is not None else None)
    if methodology_evidence:
        evidence.extend(methodology_evidence)
        formula_input = CalculationInput(
            label=f"External method reference · {methodology.source_name} · page 13",
            value=methodology.fields.get("formula_summary") or methodology.fields["formula"],
            evidence_ids=methodology_evidence)
        calculation_inputs.append(formula_input)
    calculation = CalculationDetail(
        label="Experience modification calculation and source reconciliation",
        formula=formula,
        result=None,
        rationale=(
            (f"The dated application/support values agree at {support_factor:.2f}; the draft quote "
             f"uses {quote_factor:.2f}, a {factor_delta:.2f}-point difference. "
             if factor_delta is not None else "The submitted factor values differ. ")
            + ("The quoted value also matches a prior-term value in the rating history. "
               if quote_matches_prior else "")
            + "The NCCI formula compares actual losses with expected losses for similarly classified "
              "risks. The submitted loss/payroll data do not include a complete, validated bureau "
              "worksheet with the effective-period, expected loss rates, D-ratios, claim limits, "
              "weight, ballast, and other state-plan inputs needed to independently calculate M. "
              "Therefore this is a cross-document factor conflict, not a recalculated official mod."),
        inputs=calculation_inputs,
        limitations=[
            "No official experience modifier was reconstructed from the submitted loss history.",
            "The included NCCI material explains the method; it does not authenticate this insured's factor.",
            "The rating workbook labels its factors as synthetic test values; obtain the bureau-issued worksheet.",
        ] + ([calculation_issue] if calculation_issue else []),
    )
    if selected:
        sources.append(ComparisonSource(
            document_id="CALCULATED-EMOD",
            document_name="Effective-date factor calculation",
            label="Selected verified factor", value=selected.value,
            status="VERIFIED", evidence_ids=evidence_for(selected)))
    if methodology and methodology_evidence:
        sources.append(ComparisonSource(
            document_id=methodology.document_id,
            document_name=methodology.source_name,
            label="External rating methodology",
            value=methodology.fields.get("formula_summary") or methodology.fields["formula"],
            status="REFERENCE", evidence_ids=methodology_evidence))
    if factor_delta is not None:
        factor_delta_pct = round(factor_delta / support_factor * 100, 2) if support_factor else None
        delta_formula = (f"{support_factor:.2f} current submitted factor - "
                         f"{quote_factor:.2f} quoted factor = {factor_delta:.2f}; "
                         f"quote is {factor_delta_pct:.2f}% below current submitted factor"
                         if factor_delta_pct is not None else None)
    else:
        factor_delta_pct = None
        delta_formula = None
    return [_draft(
        ctx, finding_type="EXPERIENCE_MOD_MISMATCH", taxonomy_id="P6",
        title=("Draft quote applies the prior-term experience factor"
               if support_quote_mismatch and quote_matches_prior else
               "Quoted experience modification differs from current supporting value"
               if support_quote_mismatch else
               "Experience modification values differ; applicable factor needs validation"),
        classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
        conclusion_state=conclusion,
        materiality_band=MaterialityBand.HIGH,
        materiality_rationale="Experience mod directly multiplies WC premium",
        evidence_ids=evidence,
        comparison=ctx.comparison(
            [f.value for f in observed], entity_alignment="MATCHED",
            period_alignment="MATCHED" if eff else "UNRESOLVED",
            delta=factor_delta, formula=delta_formula, base=support_factor).model_copy(
                update={"sources": sources, "calculation": calculation}),
        uncertainty_reason=(uncertainty + " The observed factor comparison requires validation."
                            if uncertainty else "The observed factor comparison requires validation."),
        possible_consequence="Quote may apply an incorrect experience modification: "
                             + "; ".join(detail_lines),
        recommended_action="Obtain the current bureau experience-mod worksheet for the proposed "
                           "effective date and validate the factor; do not select a factor based "
                           "on recency alone",
        deduplication_key="P6:emod")]


# ---------------------------------------------------------------------
# P7 - declared operations vs external/public context
# ---------------------------------------------------------------------

_STOP = {"and", "or", "the", "of", "a", "an", "to", "in", "for", "with", "on", "at", "by"}


def _keywords(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2 and t not in _STOP}


def p7_operations_context(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    declared_text = " ".join(
        [n.text for n in case.narratives]
        + [e.description or "" for e in case.exposures
           if e.exposure_type in {"declared_operation", "quote_operation"}])
    declared_kw = _keywords(declared_text)
    drafts: List[FindingDraft] = []
    for obs in case.external_observations:
        services = obs.fields.get("services") or obs.fields.get("operations") or []
        if isinstance(services, str):
            services = [services]
        if not services:
            continue
        name_state = match_names(
            obs.fields.get("entity_name"),
            next((e.legal_name for e in case.entities if e.entity_role == "insured"), None),
        )
        undeclared = [s for s in services if not (_keywords(str(s)) & declared_kw)]
        if not undeclared:
            continue
        evidence = _ev(obs, "record")
        for exp in case.exposures:
            if exp.exposure_type in {"declared_operation", "quote_operation"}:
                evidence.extend(_ev(exp, "description"))
        for n in case.narratives[:2]:
            evidence.extend(_ev(n, "text"))
        if name_state.state == "PARTIAL":
            uncertainty = (f"External record matched on name only ({name_state.rationale}); "
                           "company-wide public context does not prove exposure at a specific "
                           "insured premise")
        elif name_state.state == "UNRESOLVED":
            uncertainty = "External record identity unresolved; possible different entity"
        else:
            uncertainty = None
        drafts.append(_draft(
            ctx, finding_type="UNDECLARED_OPERATION_SIGNAL", taxonomy_id="P7",
            title=f"External/public context describes operations not declared: "
                  f"{', '.join(map(str, undeclared[:4]))}",
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=ConclusionState.POSSIBLE_MISMATCH,
            materiality_band=MaterialityBand.MEDIUM,
            materiality_rationale="Undeclared operations may be unpriced or excluded",
            evidence_ids=evidence,
            uncertainty_reason=uncertainty,
            possible_consequence="Operations described publicly are not reflected in the "
                                 "application or quote operations",
            recommended_action="Ask the broker/insured whether these services are performed, "
                               "at which premises, and whether the quote covers them",
            deduplication_key=f"P7:{obs.record_id}"))
    return drafts


# ---------------------------------------------------------------------
# P11 - exposure may exceed quoted limit / conflict with quote terms
# ---------------------------------------------------------------------

_COVERAGE_ALIASES = {
    "cargo_value": {"cargo", "inland_marine", "motor_truck_cargo", "motor truck cargo"},
}


def p11_exposure_vs_limit(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    limits = [t for t in case.quote_terms if t.term_type == "limit" and isinstance(t.value, (int, float))]
    exclusions = [t for t in case.quote_terms if t.term_type in {"exclusion", "endorsement"}]
    forms_supplied = any(t.term_type == "form" for t in case.quote_terms)
    for exp in case.exposures:
        if not isinstance(exp.value, (int, float)):
            continue
        coverages = _COVERAGE_ALIASES.get(exp.exposure_type, set())
        if not coverages:
            continue
        matching = [t for t in limits if (t.coverage or "").lower() in coverages]
        for term in matching:
            if exp.value <= float(term.value):
                continue
            delta = float(exp.value) - float(term.value)
            evidence = _ev(exp, "value") + _ev(term, "value")
            related_exclusions = [t for t in exclusions if (t.coverage or "").lower() in coverages]
            for t in related_exclusions:
                evidence.extend(_ev(t, "value"))
            limitation = ("" if forms_supplied else
                          " Coverage-fit review limited: applicable form/endorsement wording "
                          "not supplied.")
            drafts.append(_draft(
                ctx, finding_type="EXPOSURE_EXCEEDS_LIMIT", taxonomy_id="P11",
                title=f"Evidence-backed {exp.exposure_type} ({exp.value:,.0f}) exceeds quoted "
                      f"{term.coverage} limit ({float(term.value):,.0f})",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                materiality_band=MaterialityBand.HIGH,
                materiality_rationale="Potential under-insurance of an evidence-backed peak exposure",
                evidence_ids=evidence,
                comparison=ctx.comparison(
                    [exp.value, float(term.value)], delta=delta, base=float(term.value),
                    formula=f"exposure {exp.value} - limit {term.value}",
                    entity_alignment="MATCHED", period_alignment="PARTIAL"),
                uncertainty_reason="Data inconsistency confirmed; coverage outcome requires "
                                   "specialist interpretation of policy wording",
                possible_consequence=f"Peak exposure exceeds the quoted limit by {delta:,.0f} "
                                     f"({(delta / float(term.value) * 100):.1f}%).{limitation}",
                recommended_action="Refer to a coverage specialist; confirm peak values with the "
                                   "insured and consider limit adequacy. No legal coverage "
                                   "conclusion is made.",
                deduplication_key=f"P11:{exp.exposure_type}:{term.coverage}"))
    return drafts


# ---------------------------------------------------------------------
# N3 - WC state allocation
# ---------------------------------------------------------------------

def n3_wc_state_allocation(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    app_ids = {d.document_id for d in case.documents_of_type("acord_130")}
    wb_ids = {d.document_id for d in case.documents_of_type("wc_rating_workbook")}
    app_states = {r.state for r in case.wc_payroll_rows if r.document_id in app_ids and r.state}
    wb_rows = [r for r in case.wc_payroll_rows if r.document_id in wb_ids]
    wb_states = {r.state for r in wb_rows if r.state}

    # Compare the actual form/workbook schedule coverage at the same
    # location/class grain. An ACORD 130 may contain fewer rows than a
    # supporting workbook even when the known state labels happen to agree.
    def location_key(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return _location_count_key(LocationRecord(
            record_id=str(value), document_id="wc_schedule",
            location_id=str(value)))

    app_rows = [r for r in case.wc_payroll_rows if r.document_id in app_ids]
    app_schedule: Dict[tuple, list] = defaultdict(list)
    workbook_schedule: Dict[tuple, list] = defaultdict(list)
    for row in app_rows:
        key = location_key(row.location_id)
        if key and row.class_code:
            app_schedule[(key, normalize_class_code(row.class_code))].append(row)
    for row in wb_rows:
        key = location_key(row.location_id)
        if key and row.class_code:
            workbook_schedule[(key, normalize_class_code(row.class_code))].append(row)
    missing_schedule_keys = set(workbook_schedule) - set(app_schedule)
    if missing_schedule_keys:
        missing_labels = sorted({
            f"{row.location_id or 'location unspecified'} / class {row.class_code}"
            for key in missing_schedule_keys for row in workbook_schedule[key]
        })
        evidence: List[str] = []
        for key in missing_schedule_keys:
            for row in workbook_schedule[key]:
                evidence.extend(_ev(row, "location_id", "class_code", "payroll",
                                    "full_time", "part_time"))
        # Cite the full set of form rows at affected locations so the missing
        # location/class comparison can be checked against what was supplied.
        affected_locations = {key[0] for key in missing_schedule_keys}
        for key, rows in app_schedule.items():
            if key[0] in affected_locations:
                for row in rows:
                    evidence.extend(_ev(row, "location_id", "class_code", "payroll"))
        drafts.append(_draft(
            ctx, finding_type="WC_CLASS_SCHEDULE_INCOMPLETE", taxonomy_id="N3",
            title=(f"ACORD 130 omits {len(missing_schedule_keys)} location/class "
                   f"schedule row(s) present in the WC workbook: "
                   + "; ".join(missing_labels[:8])),
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=ConclusionState.POSSIBLE_MISMATCH,
            materiality_band=MaterialityBand.MEDIUM,
            materiality_rationale="Missing location/class rows can leave WC exposure allocations incomplete",
            evidence_ids=list(dict.fromkeys(evidence)),
            comparison=ctx.comparison(
                [len(app_schedule), len(workbook_schedule)],
                delta=len(workbook_schedule) - len(app_schedule),
                formula="distinct location/class rows in WC workbook minus ACORD 130",
                entity_alignment="PARTIAL", period_alignment="PARTIAL"),
            uncertainty_reason="Workbook rows establish supporting allocations; omitted application rows require confirmation",
            possible_consequence="WC payroll or classifications may not be fully represented on the application",
            recommended_action="Reconcile each location and class row across the ACORD 130 and supporting WC workbook",
            deduplication_key="N3:location-class-schedule"))

    if app_states and wb_states:
        missing_rows = wb_states - app_states
        if missing_rows:
            evidence: List[str] = []
            for r in wb_rows:
                if r.state in missing_rows:
                    evidence.extend(_ev(r, "state", "class_code", "payroll"))
            drafts.append(_draft(
                ctx, finding_type="WC_STATE_ALLOCATION_INCOMPLETE", taxonomy_id="N3",
                title=f"ACORD 130 schedule omits workbook state(s): {', '.join(sorted(missing_rows))}",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                materiality_band=MaterialityBand.MEDIUM,
                materiality_rationale="Incomplete state schedule can omit exposure from review",
                evidence_ids=evidence[:12],
                comparison=ctx.comparison(
                    [sorted(app_states), sorted(wb_states)],
                    entity_alignment="MATCHED", period_alignment="PARTIAL"),
                possible_consequence="Workbook allocates payroll to states not shown on the application",
                recommended_action="Confirm where work is performed and correct the ACORD 130 schedule",
                deduplication_key="N3:missing-states"))

    # Compare the supporting WC workbook's state at each stable location ID
    # against the property schedule. Comparing only account-wide state sets
    # misses the actual allocation conflict and cannot show which premises
    # disagree.
    sov_ids = {d.document_id for d in case.documents_of_type("sov")}
    sov_by_location: Dict[str, List[LocationRecord]] = defaultdict(list)
    for location in case.locations:
        if location.document_id in sov_ids and location.location_id and location.state:
            sov_by_location[_location_count_key(location)].append(location)
    workbook_by_location: Dict[str, List[WCPayrollRow]] = defaultdict(list)
    for row in wb_rows:
        if row.location_id and row.state:
            workbook_by_location[location_key(row.location_id)].append(row)
    state_conflicts: Dict[str, tuple[set[str], set[str]]] = {}
    state_conflict_evidence: List[str] = []
    for key in set(sov_by_location) & set(workbook_by_location):
        sov_values = {normalize_state(item.state) or item.state for item in sov_by_location[key]}
        wc_values = {normalize_state(item.state) or item.state for item in workbook_by_location[key]}
        if sov_values and wc_values and sov_values.isdisjoint(wc_values):
            state_conflicts[key] = (sov_values, wc_values)
            for location in sov_by_location[key]:
                state_conflict_evidence.extend(_ev(location, "location_id", "state", "address"))
            # State is repeated on each class row; one row per location is
            # sufficient to prove the state assignment without bloating the
            # evidence bundle sent to the scope auditor.
            if workbook_by_location[key]:
                state_conflict_evidence.extend(_ev(
                    workbook_by_location[key][0], "location_id", "state", "class_code", "payroll"))
    if state_conflicts:
        conflict_labels = []
        for key, (sov_values, wc_values) in sorted(state_conflicts.items()):
            locations = sov_by_location[key]
            location_label = locations[0].location_id or locations[0].address or key
            conflict_labels.append(
                f"{location_label}: property {','.join(sorted(sov_values))} vs WC workbook "
                f"{','.join(sorted(wc_values))}")
        drafts.append(_draft(
            ctx, finding_type="WC_LOCATION_STATE_CONFLICT", taxonomy_id="N3",
            title="WC workbook state allocation conflicts with the property schedule",
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=ConclusionState.POSSIBLE_MISMATCH,
            materiality_band=MaterialityBand.MEDIUM,
            materiality_rationale="State allocation affects WC jurisdiction and rating inputs",
            evidence_ids=list(dict.fromkeys(state_conflict_evidence)),
            comparison=ctx.comparison(
                conflict_labels, entity_alignment="MATCHED", period_alignment="PARTIAL"),
            uncertainty_reason="The schedules assign different states to matching location IDs; neither source alone establishes the correct WC work state",
            possible_consequence="Payroll may be allocated to the wrong WC state if the workbook or property schedule is inaccurate",
            recommended_action="Confirm each location's physical address and employee work state, then reconcile the WC state allocation",
            deduplication_key="N3:location-state-conflicts"))

    # A location-only state with no corresponding WC location row is a
    # qualified question. Exclude states already covered by an explicit
    # location-level conflict so the same issue is not surfaced twice.
    sov_states = {l.state for locations in sov_by_location.values() for l in locations if l.state}
    wc_states = app_states | wb_states
    if sov_states and wc_states:
        conflicted_sov_states = {state for sov_values, _ in state_conflicts.values() for state in sov_values}
        uncovered = sov_states - wc_states - conflicted_sov_states
        if uncovered:
            evidence = []
            for loc in case.locations:
                if loc.state in uncovered:
                    evidence.extend(_ev(loc, "address", "state"))
            drafts.append(_draft(
                ctx, finding_type="WC_STATE_COVERAGE_QUESTION", taxonomy_id="N3",
                title=f"Property schedule lists state(s) with no WC payroll rows: "
                      f"{', '.join(sorted(uncovered))}",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.POSSIBLE_MISMATCH,
                severity=Severity.LOW,
                materiality_band=MaterialityBand.LOW,
                materiality_rationale="A property address alone does not establish work state",
                evidence_ids=evidence[:10],
                uncertainty_reason="Address presence does not prove employees work in that state",
                possible_consequence="Potential unscheduled WC exposure if employees work at these premises",
                recommended_action="Ask whether employees work at these locations; request state "
                                   "allocation if so",
                deduplication_key="N3:sov-states"))
    return drafts


# ---------------------------------------------------------------------
# N4 - employee counts across forms
# ---------------------------------------------------------------------

def n4_employee_counts(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    rows_by_type: Dict[str, Dict[tuple, list]] = {}
    for doc_type in ("acord_130", "wc_rating_workbook"):
        doc_ids = {d.document_id for d in case.documents_of_type(doc_type)}
        grouped: Dict[tuple, list] = defaultdict(list)
        for row in case.wc_payroll_rows:
            if (row.document_id not in doc_ids or not row.location_id
                    or not row.class_code):
                continue
            # Compare only equivalent populations: same location, class and
            # effective period. Do not compare an application-wide headcount
            # with a class subset or sums across differently sized schedules.
            location_key_value = _location_count_key(LocationRecord(
                record_id=str(row.location_id), document_id=row.document_id,
                location_id=str(row.location_id)))
            key = (location_key_value,
                   normalize_class_code(row.class_code), row.effective_date)
            grouped[key].append(row)
        rows_by_type[doc_type] = grouped

    for left_type, right_type in (("acord_130", "wc_rating_workbook"),):
        left = rows_by_type.get(left_type, {})
        right = rows_by_type.get(right_type, {})
        for key in sorted(set(left) & set(right), key=str):
            left_rows, right_rows = left[key], right[key]
            for kind, label, attr, evidence_field in (
                    ("ft", "full-time", "full_time_count", "full_time"),
                    ("pt", "part-time", "part_time_count", "part_time")):
                values_left = [getattr(row, attr) for row in left_rows
                               if getattr(row, attr) is not None]
                values_right = [getattr(row, attr) for row in right_rows
                                if getattr(row, attr) is not None]
                if not values_left or not values_right:
                    continue
                value_left, value_right = sum(values_left), sum(values_right)
                delta = value_left - value_right
                if ctx.within_tolerance(delta, value_right):
                    continue
                evidence_left = list(dict.fromkeys(
                    eid for row in left_rows
                    for eid in _ev(row, "location_id", "class_code", evidence_field)))
                evidence_right = list(dict.fromkeys(
                    eid for row in right_rows
                    for eid in _ev(row, "location_id", "class_code", evidence_field)))
                location = left_rows[0].location_id or key[0]
                class_code = left_rows[0].class_code or key[1]
                left_name = _doc_label(case, left_rows[0].document_id)
                right_name = _doc_label(case, right_rows[0].document_id)
                drafts.append(_draft(
                    ctx, finding_type="EMPLOYEE_COUNT_CONFLICT", taxonomy_id="N4",
                    title=(f"{label.capitalize()} employee counts differ for location "
                           f"{location}, class {class_code}: {left_name}={int(value_left)} "
                           f"vs {right_name}={int(value_right)}"),
                    classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                    conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                    evidence_ids=evidence_left + evidence_right,
                    comparison=ctx.comparison(
                        [value_left, value_right], delta=delta, base=value_right,
                        formula=(f"{left_name} {label} {value_left} - "
                                 f"{right_name} {label} {value_right}"),
                        entity_alignment="MATCHED", period_alignment="MATCHED"
                        if key[2] else "UNRESOLVED"),
                    uncertainty_reason="Counts are aligned by location, class and available effective period; confirm the source population if schedules differ",
                    possible_consequence="Headcount-dependent WC review inputs may be inconsistent for this class and location",
                    recommended_action="Confirm full/part-time counts for the same location and class across both schedules",
                    deduplication_key=f"N4:{kind}:{key[0]}:{key[1]}:{key[2] or 'period-unknown'}"))
    return drafts


# ---------------------------------------------------------------------
# N5 - annual claim counts vs loss detail
# ---------------------------------------------------------------------

def _lob_key(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = re.sub(r"[^a-z]+", " ", text.lower()).strip()
    if not t:
        return None
    if "worker" in t or t == "wc":
        return "workers compensation"
    if "auto" in t:
        return "commercial auto"
    if "general liability" in t or t == "gl":
        return "general liability"
    if "property" in t:
        return "property"
    if "cargo" in t:
        return "motor truck cargo"
    return t or None


def n5_annual_claim_counts(ctx: RuleContext) -> List[FindingDraft]:
    """Annual claim-count reconciliation, aligned by policy year AND line of
    business — a form's WC history is never compared against all-line detail
    counts (unified register U08: compare like-for-like scope)."""
    case = ctx.case
    loss_doc_ids = {d.document_id for d in case.documents_of_type("loss_run")}
    claims = [c for c in case.claims if c.document_id in loss_doc_ids]
    detail_by_year: Dict[str, List] = defaultdict(list)
    detail_by_year_lob: Dict[Tuple[str, str], List] = defaultdict(list)
    for c in claims:
        year = (c.policy_year or (c.loss_date or "")[:4])
        if not year:
            continue
        detail_by_year[year].append(c)
        lob = _lob_key(c.line_of_business)
        if lob:
            detail_by_year_lob[(year, lob)].append(c)

    def distinct_claim_count(rows: List) -> int:
        # Duplicate detail rows for one claim are a separate U08/P5 issue;
        # they must not inflate the annual claim frequency comparison.
        identities = {c.claim_id.strip().casefold() if c.claim_id else c.record_id for c in rows}
        return len(identities)
    doc_type_by_id = {d.document_id: d.document_type for d in case.documents}
    declared: List[Tuple[str, str, Optional[str], float, List[str], bool]] = []
    for exp in case.exposures:
        if exp.exposure_type != "annual_claim_count" or exp.value is None or not exp.period:
            continue
        doc_type = doc_type_by_id.get(exp.document_id, "")
        src = "loss_summary" if exp.document_id in loss_doc_ids else f"{doc_type or 'acord'} history"
        description = (exp.description or "").casefold()
        lob = _lob_key(exp.description)
        all_lines = "all line" in description
        if lob is None and doc_type == "acord_130":
            lob = "workers compensation"
        declared.append((
            src, exp.period, lob, float(exp.value), _ev(exp, "count"), all_lines))
    drafts: List[FindingDraft] = []
    seen: set = set()
    for src, year, lob, count, evidence, all_lines in declared:
        if lob is not None:
            matched_rows = detail_by_year_lob.get((year, lob), [])
            scope = f"{lob} "
        elif all_lines:
            matched_rows = detail_by_year.get(year, [])
            scope = "all-lines "
        else:
            matched_rows = []  # unspecified scope is not comparable to any population
            scope = ""
        if not matched_rows:
            continue  # no comparable detail population; never force a match
        actual = distinct_claim_count(matched_rows)
        if int(count) == actual:
            continue
        if not evidence:
            continue  # only create the finding when independently evidenced
        key = (src, year, lob)
        if key in seen:
            continue
        seen.add(key)
        drafts.append(_draft(
            ctx, finding_type="ANNUAL_CLAIM_COUNT_MISMATCH", taxonomy_id="N5",
            title=f"{src} reports {int(count)} {scope}claims for {year}; "
                  f"loss detail contains {actual}",
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
            evidence_ids=list(dict.fromkeys(
                evidence + [eid for claim in matched_rows
                            for eid in _claim_reconciliation_evidence(
                                claim, "claim_id", "loss_date", "line_of_business", "policy_year")]
            )),
            comparison=ctx.comparison(
                [count, actual], delta=count - actual, base=actual,
                formula=f"{src} {year} {scope}count {int(count)} - loss detail {actual}",
                entity_alignment="MATCHED",
                period_alignment="MATCHED" if year[:4] == year else "PARTIAL"),
            uncertainty_reason="Policy-year vs calendar-year basis assumed; confirm if disputed",
            possible_consequence="Annual claim frequency used in underwriting may be misstated",
            recommended_action="Reconcile the annual claim counts with the loss detail listing",
            deduplication_key=f"N5:{src}:{year}:{lob or 'all'}"))
    return drafts


# ---------------------------------------------------------------------
# N6 - GL sales and area rating bases
# ---------------------------------------------------------------------

def n6_gl_bases(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    doc126 = {d.document_id for d in case.documents_of_type("acord_126")}
    doc125 = {d.document_id for d in case.documents_of_type("acord_125")}
    quote_ids = {d.document_id for d in case.documents_of_type("quote")}
    sales_125 = [e for e in case.exposures if e.exposure_type == "sales"
                 and e.document_id in doc125 and e.value is not None]
    sales_rows_126 = [e for e in case.exposures if e.exposure_type == "sales_rated" and e.document_id in doc126]
    sales_quote = [e for e in case.exposures if e.exposure_type == "sales"
                  and e.document_id in quote_ids and e.value is not None]
    if sales_125 and sales_rows_126:
        declared = float(sales_125[0].value)
        summed = sum(float(e.value) for e in sales_rows_126 if e.value is not None)
        delta = declared - summed
        if not ctx.within_tolerance(delta, declared):
            evidence = _ev(sales_125[0], "value")
            for e in sales_rows_126[:8]:
                evidence.extend(_ev(e, "value"))
            drafts.append(_draft(
                ctx, finding_type="GL_SALES_BASIS_UNRECONCILED", taxonomy_id="N6",
                title="GL gross sales not reconciled to sales-rated exposure rows",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.POSSIBLE_MISMATCH,
                materiality_band=MaterialityBand.MEDIUM,
                materiality_rationale="Sales are the GL rating base",
                evidence_ids=evidence,
                comparison=ctx.comparison(
                    [declared, summed], delta=delta, base=declared,
                    formula=f"declared sales {declared} - sum(sales-rated rows) {summed}",
                    entity_alignment="MATCHED", period_alignment="PARTIAL"),
                uncertainty_reason="Unexplained remainder is not assumed to be missing sales: "
                                   "allocation, exclusions, or alternate bases may explain it",
                possible_consequence="GL premium base may be incomplete or double-counted",
                recommended_action="Request the sales allocation supporting the rated exposures",
                deduplication_key="N6:sales"))
    if sales_125 and sales_quote:
        a, b = float(sales_125[0].value), float(sales_quote[0].value)
        delta = a - b
        if not ctx.within_tolerance(delta, a):
            drafts.append(_draft(
                ctx, finding_type="GL_SALES_QUOTE_MISMATCH", taxonomy_id="N6",
                title=f"Gross sales: application {a:,.0f} vs quote {b:,.0f}",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                evidence_ids=_ev(sales_125[0], "value") + _ev(sales_quote[0], "value"),
                comparison=ctx.comparison(
                    [a, b], delta=delta, base=a, formula=f"application {a} - quote {b}",
                    entity_alignment="MATCHED", period_alignment="PARTIAL"),
                possible_consequence="Quote may be rated on different sales than declared",
                recommended_action="Confirm the sales figure and period used for rating",
                deduplication_key="N6:sales-quote"))
    # area-rated square footage (compare per source document, not the
    # cross-document grand total — each document schedules the same premises)
    area_126 = [e for e in case.exposures if e.exposure_type == "area_rated" and e.document_id in doc126]
    sqft_by_doc: Dict[str, List[LocationRecord]] = defaultdict(list)
    for loc in case.locations:
        if loc.square_footage is not None:
            sqft_by_doc[loc.document_id].append(loc)
    if area_126 and sqft_by_doc:
        rated = sum(float(e.value) for e in area_126 if e.value is not None)
        doc_sums = {d: sum(l.square_footage or 0 for l in locs)
                    for d, locs in sqft_by_doc.items()}
        if not any(ctx.within_tolerance(rated - total, total) for total in doc_sums.values()):
            scheduled = next(iter(doc_sums.values()))
            delta = rated - scheduled
            evidence: List[str] = []
            for e in area_126[:6]:
                evidence.extend(_ev(e, "value"))
            for l in next(iter(sqft_by_doc.values()))[:6]:
                evidence.extend(_ev(l, "square_footage"))
            drafts.append(_draft(
                ctx, finding_type="GL_AREA_BASIS_UNRECONCILED", taxonomy_id="N6",
                title="Area-rated square footage does not reconcile to scheduled premises area",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.POSSIBLE_MISMATCH,
                evidence_ids=evidence,
                comparison=ctx.comparison(
                    [rated, scheduled], delta=delta, base=scheduled,
                    formula=f"area-rated {rated} - scheduled {scheduled}",
                    entity_alignment="PARTIAL", period_alignment="UNRESOLVED"),
                uncertainty_reason="Rated and scheduled areas may use different scopes",
                possible_consequence="Area-based rating may not match the scheduled premises",
                recommended_action="Confirm area-rated exposure bases and scope",
                deduplication_key="N6:area"))
    return drafts


# ---------------------------------------------------------------------
# N7 - fleet garaging-state conflicts
# ---------------------------------------------------------------------

_STATE_TOKEN = re.compile(r"\b([A-Z]{2})\b")


def n7_garaging_state(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    fleet_ids = {d.document_id for d in case.documents_of_type("fleet_schedule")}
    conflicts = []
    for v in case.vehicles:
        if v.document_id not in fleet_ids or not v.garaging_state or not v.garaging_address:
            continue
        tokens = _STATE_TOKEN.findall(v.garaging_address.upper())
        addr_state = None
        for token in reversed(tokens):
            if normalize_state(token):
                addr_state = normalize_state(token)
                break
        if addr_state and addr_state != v.garaging_state:
            conflicts.append((v, addr_state))
    drafts: List[FindingDraft] = []
    for v, addr_state in conflicts:
        drafts.append(_draft(
            ctx, finding_type="GARAGING_STATE_CONFLICT", taxonomy_id="N7",
            title=f"Unit {v.unit_id}: garaging state code {v.garaging_state} conflicts with "
                  f"garaging address state {addr_state}",
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
            materiality_band=MaterialityBand.MEDIUM,
            materiality_rationale="Garaging state drives territory/rating",
            evidence_ids=_ev(v, "garaging_state", "garaging_address", "vin"),
            comparison=ctx.comparison(
                [v.garaging_state, addr_state], entity_alignment="MATCHED",
                period_alignment="MATCHED"),
            possible_consequence="Territory/rating may be wrong for this unit; neither field is "
                                 "overwritten automatically",
            recommended_action="Confirm the actual garaging address and state for the unit",
            deduplication_key=f"N7:{v.unit_id}"))
    return drafts


# ---------------------------------------------------------------------
# N8 - heavy-vehicle driver qualification
# ---------------------------------------------------------------------

_HEAVY_GVWR = 26001.0
_CDL_PATTERN = re.compile(r"^(cdl|class\s*[ab]|cdl[- ]?[ab])", re.IGNORECASE)


def n8_driver_qualification(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    fleet_ids = {d.document_id for d in case.documents_of_type("fleet_schedule")}
    heavy = [v for v in case.vehicles
             if v.document_id in fleet_ids and v.gvwr is not None and v.gvwr >= _HEAVY_GVWR]
    if not heavy:
        return []
    if not case.has_document_type("driver_roster"):
        evidence: List[str] = []
        for v in heavy[:5]:
            evidence.extend(_ev(v, "gvwr", "assigned_driver_id"))
        return [_draft(
            ctx, finding_type="HEAVY_VEHICLE_DRIVER_REVIEW_GAP", taxonomy_id="N8",
            title=f"{len(heavy)} heavy vehicle(s) (GVWR >= {int(_HEAVY_GVWR)}) but no driver "
                  "roster/credentials supplied",
            classification=FindingClassification.EVIDENCE_GAP,
            conclusion_state=ConclusionState.EVIDENCE_GAP,
            materiality_band=MaterialityBand.MEDIUM,
            materiality_rationale="Qualification of heavy-vehicle drivers cannot be reviewed",
            evidence_ids=evidence,
            uncertainty_reason="Absent roster/credentials is an evidence gap, not a confirmed "
                               "qualification mismatch",
            possible_consequence="Heavy-vehicle driver qualification cannot be verified",
            recommended_action="Request driver roster with license class/CDL endorsements for "
                               "assigned drivers of heavy units",
            deduplication_key="N8:no-roster")]
    drivers = {d.driver_id: d for d in case.drivers}
    drafts = []
    for v in heavy:
        if not v.assigned_driver_id:
            continue
        driver = drivers.get(v.assigned_driver_id)
        if driver is None:
            drafts.append(_draft(
                ctx, finding_type="HEAVY_VEHICLE_DRIVER_UNKNOWN", taxonomy_id="N8",
                title=f"Unit {v.unit_id}: assigned driver {v.assigned_driver_id} not on roster",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.POSSIBLE_MISMATCH,
                evidence_ids=_ev(v, "gvwr", "assigned_driver_id"),
                uncertainty_reason="Driver record not found; identity unresolved",
                possible_consequence="Assigned driver credentials cannot be verified",
                recommended_action="Confirm the assigned driver and obtain credentials",
                deduplication_key=f"N8:unknown:{v.unit_id}"))
            continue
        if driver.license_class and not _CDL_PATTERN.match(driver.license_class.strip()):
            drafts.append(_draft(
                ctx, finding_type="HEAVY_VEHICLE_QUALIFICATION_UNVERIFIED", taxonomy_id="N8",
                title=f"Unit {v.unit_id} (GVWR {v.gvwr:,.0f}) qualification is unverified: "
                      f"roster lists license class '{driver.license_class}', but applicable "
                      "qualification criteria are not supplied",
                classification=FindingClassification.EVIDENCE_GAP,
                conclusion_state=ConclusionState.EVIDENCE_GAP,
                materiality_band=MaterialityBand.LOW,
                materiality_rationale="Roster class alone does not establish a qualification exception",
                evidence_ids=_ev(v, "gvwr", "assigned_driver_id") + _ev(driver, "license_class", "name"),
                uncertainty_reason="No applicable jurisdiction or carrier qualification criteria "
                                   "were supplied; no disqualification is established",
                possible_consequence="Driver-to-vehicle eligibility cannot be independently verified",
                recommended_action="Obtain applicable jurisdiction/carrier criteria and supporting "
                                   "credential or MVR results, then verify the assignment",
                deduplication_key=f"N8:license:{v.unit_id}"))
    return drafts


# ---------------------------------------------------------------------
# N14 - external record premises matching
# ---------------------------------------------------------------------

def n14_external_record_match(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    insured = next((e for e in case.entities if e.entity_role == "insured"), None)
    for obs in case.external_observations:
        if "osha" not in obs.source_name.lower() and obs.subject not in {"inspection", "violation"}:
            continue
        name_state = match_names(obs.fields.get("entity_name"),
                                 insured.legal_name if insured else None)
        loc_state = None
        obs_addr = obs.fields.get("address")
        summary_text = " ".join(str(value) for value in
                                (obs.fields.get("services") or []) if value)
        address_match = re.search(
            r"\b\d{1,7}\s+[^,;]+,\s*[^,;]+,\s*([A-Z]{2})\s+\d{5}(?:-\d{4})?\b",
            summary_text, re.IGNORECASE)
        if not obs_addr and address_match:
            obs_addr = address_match.group(0).strip().rstrip(".;")
        obs_state = normalize_state(obs.fields.get("state"))
        if not obs_state and address_match:
            obs_state = normalize_state(address_match.group(1))
        scheduled_states = {normalize_state(location.state) for location in case.locations
                            if location.state and normalize_state(location.state)}
        # A same-name public record at a known, different state is not evidence
        # about any of this submission's scheduled premises. Retain it as
        # source context, but don't emit a premises-level finding.
        if obs_state and scheduled_states and obs_state not in scheduled_states:
            continue
        if obs_addr:
            candidate = LocationRecord(record_id="ext", document_id=obs.document_id,
                                       address=obs_addr,
                                       city=obs.fields.get("city"),
                                       state=obs_state,
                                       postal_code=obs.fields.get("zip"))
            rank = {"MATCHED": 2, "PARTIAL": 1, "UNRESOLVED": 0}
            results = [match_locations(candidate, l) for l in case.locations]
            loc_state = max(results, key=lambda r: rank[r.state]) if results else None
        evidence = _ev(obs, "record")
        if insured is not None:
            evidence.extend(_ev(insured, "legal_name"))
        if name_state.state == "UNRESOLVED" and (loc_state is None or loc_state.state == "UNRESOLVED"):
            drafts.append(_draft(
                ctx, finding_type="EXTERNAL_RECORD_UNMATCHED", taxonomy_id="N14",
                title=f"External {obs.source_name} record could not be matched to an insured "
                      "entity or premises",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.POSSIBLE_MISMATCH,
                severity=Severity.LOW,
                materiality_band=MaterialityBand.LOW,
                materiality_rationale="Unmatched record has no established bearing on this risk",
                evidence_ids=evidence,
                uncertainty_reason="A non-match does not prove the submission declaration is "
                                   "false, and the record is not attributed to any insured premise",
                possible_consequence="None established; record retained for reviewer awareness",
                recommended_action="Confirm whether the record concerns this insured; obtain "
                                   "registration/address identifiers",
                deduplication_key=f"N14:{obs.record_id}"))
        elif name_state.state == "PARTIAL" and (loc_state is None or loc_state.state != "MATCHED"):
            drafts.append(_draft(
                ctx, finding_type="EXTERNAL_RECORD_POSSIBLE_MATCH", taxonomy_id="N14",
                title=f"External {obs.source_name} record name-matches the insured but premises "
                      "identity is unresolved",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.POSSIBLE_MISMATCH,
                materiality_band=MaterialityBand.MEDIUM,
                materiality_rationale="A public record is not evidence of conditions at a "
                                      "different/unmatched premises",
                evidence_ids=evidence,
                uncertainty_reason="Name-only match; establishment-level identity not confirmed",
                possible_consequence="Record may relate to a different establishment of a "
                                     "similarly named entity",
                recommended_action="Verify establishment address/registration before attributing "
                                   "the record to a scheduled premise",
                deduplication_key=f"N14:{obs.record_id}"))
    return drafts


# ---------------------------------------------------------------------
# N15 - WC class-rate discrepancy
# ---------------------------------------------------------------------

def n15_class_rate(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    rates: Dict[tuple, list] = defaultdict(list)
    location_states: Dict[str, set[str]] = defaultdict(set)
    locations_by_key: Dict[str, list] = defaultdict(list)
    for location in case.locations:
        if not location.location_id:
            continue
        key = _location_count_key(location)
        locations_by_key[key].append(location)
        if location.state:
            location_states[key].add(normalize_state(location.state) or location.state)
    payroll_by_record = {row.record_id: row for row in case.wc_payroll_rows}
    rate_sources: List[tuple[str, str, Optional[str], Optional[str], Optional[float], str, List[str]]] = []
    # Some adapters expose class rates as standalone rating factors; others
    # keep them on the source payroll/class row. Treat both as equivalent
    # observations so a missing typed factor does not hide a real comparison.
    for f in case.rating_factors:
        if f.factor_type == "class_rate" and f.value is not None and f.class_code:
            rate_sources.append((f.document_id, f.record_id, f.class_code, f.state,
                                 f.value, f.source_authority or "unknown", _ev(f, "value")))
    for row in case.wc_payroll_rows:
        if row.rate is not None and row.class_code:
            rate_sources.append((row.document_id, row.record_id, row.class_code, row.state,
                                 row.rate, "unknown", _ev(row, "rate", "class_code", "state", "location_id")))
    seen_rate_observations: set[tuple] = set()
    for document_id, record_id, class_code_value, state_value, rate_value, authority, evidence in rate_sources:
        state = normalize_state(state_value) if state_value else None
        if not state and record_id.endswith("#rate"):
            source_row = payroll_by_record.get(record_id[:-5])
            if source_row:
                state = normalize_state(source_row.state) if source_row.state else None
                evidence.extend(_ev(source_row, "class_code", "rate", "state", "location_id"))
                state_value = source_row.state
                if not state and source_row.location_id:
                    location_key_value = _location_count_key(LocationRecord(
                        record_id=str(source_row.location_id),
                        document_id=source_row.document_id,
                        location_id=str(source_row.location_id)))
                    matching_states = location_states.get(location_key_value, set())
                    if len(matching_states) == 1:
                        state = next(iter(matching_states))
                        for location in locations_by_key.get(location_key_value, []):
                            evidence.extend(_ev(location, "location_id", "state", "address"))
        if not state and document_id in {r.document_id for r in case.wc_payroll_rows}:
            source_row = next((r for r in case.wc_payroll_rows if r.record_id == record_id), None)
            if source_row and source_row.location_id:
                location_key_value = _location_count_key(LocationRecord(
                    record_id=str(source_row.location_id), document_id=source_row.document_id,
                    location_id=str(source_row.location_id)))
                matching_states = location_states.get(location_key_value, set())
                if len(matching_states) == 1:
                    state = next(iter(matching_states))
                    for location in locations_by_key.get(location_key_value, []):
                        evidence.extend(_ev(location, "location_id", "state", "address"))
        if state:
            class_key = normalize_class_code(class_code_value) or class_code_value
            observation_key = (document_id, class_key, state, round(float(rate_value), 6))
            if observation_key in seen_rate_observations:
                continue
            seen_rate_observations.add(observation_key)
            rates[(class_key, state)].append({
                "document_id": document_id, "record_id": record_id, "value": float(rate_value),
                "authority": authority, "evidence": list(dict.fromkeys(evidence)),
            })
    drafts: List[FindingDraft] = []
    for (class_code, state), rows in sorted(rates.items()):
        distinct = {round(item["value"], 4) for item in rows}
        if len(distinct) < 2:
            continue
        authoritative = [r for r in rows
                         if (r["authority"] or "").lower()
                         in {"bureau", "carrier_filed", "ncci", "wcirb"}]
        evidence: List[str] = []
        values = []
        for item in rows:
            evidence.extend(item["evidence"])
            values.append(f"{item['document_id']}: {item['value']} (source {item['authority']})")
        conclusion = (ConclusionState.CONFIRMED_DATA_CONFLICT if authoritative
                      else ConclusionState.POSSIBLE_MISMATCH)
        uncertainty = None if authoritative else (
            "No approved filed-rate source supplied; a workbook or quote rate is not "
            "authenticated rate support")
        drafts.append(_draft(
            ctx, finding_type="WC_CLASS_RATE_DISCREPANCY", taxonomy_id="N15",
            title=f"WC class {class_code} ({state}) rates disagree across documents",
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=conclusion,
            materiality_band=MaterialityBand.MEDIUM,
            materiality_rationale="Class rates are pricing inputs",
            evidence_ids=evidence,
            comparison=ctx.comparison(
                [item["value"] for item in rows], entity_alignment="MATCHED",
                period_alignment="PARTIAL"),
            uncertainty_reason=uncertainty,
            possible_consequence="Premium may be computed with an unvalidated rate: "
                                 + "; ".join(values),
            recommended_action="Validate the rate against approved carrier/bureau rate pages for "
                               "the state and effective date; do not assume either table is authoritative",
            deduplication_key=f"N15:{class_code}:{state}"))
    return drafts


# ---------------------------------------------------------------------
# Data-quality within-document rules
# ---------------------------------------------------------------------

def dq_duplicate_ids(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    collections = (
        ("vehicle", case.vehicles, lambda r: r.unit_id),
        ("driver", case.drivers, lambda r: r.driver_id),
        ("location", case.locations, lambda r: r.location_id),
    )
    for label, records, key_fn in collections:
        by_doc: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in records:
            key = key_fn(r)
            if key:
                by_doc[r.document_id][str(key)] += 1
        for doc_id, keys in by_doc.items():
            for key, count in keys.items():
                if count > 1:
                    drafts.append(_draft(
                        ctx, finding_type="DUPLICATE_RECORD_ID",
                        title=f"Duplicate {label} ID '{key}' appears {count} times in {doc_id}",
                        classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                        conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                        severity=Severity.LOW,
                        evidence_ids=[],
                        possible_consequence="Duplicate identifiers can distort counts",
                        recommended_action="Confirm whether rows are true duplicates or distinct records",
                        deduplication_key=f"DQ-DUP:{doc_id}:{label}:{key}"))
    return drafts


def dq_arithmetic(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    for loc in case.locations:
        if loc.building_value is None or loc.contents_value is None or loc.tiv is None:
            continue
        expected = loc.building_value + loc.contents_value
        delta = loc.tiv - expected
        if ctx.within_tolerance(delta, expected):
            continue
        drafts.append(_draft(
            ctx, finding_type="INTERNAL_TOTAL_INCONSISTENT",
            title=f"Location {loc.location_id or loc.address}: TIV does not equal building + contents",
            classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
            conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
            severity=Severity.LOW,
            evidence_ids=_ev(loc, "tiv", "building_value"),
            comparison=ctx.comparison(
                [loc.tiv, expected], delta=delta, base=expected,
                formula=f"tiv {loc.tiv} - (building {loc.building_value} + contents {loc.contents_value})",
                entity_alignment="MATCHED", period_alignment="MATCHED"),
            possible_consequence="Scheduled values are internally inconsistent",
            recommended_action="Correct the schedule of values",
            deduplication_key=f"DQ-ARITH:{loc.document_id}:{loc.location_id}"))
    return drafts


def dq_date_order(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    drafts: List[FindingDraft] = []
    for doc in case.documents:
        period = doc.effective_period or {}
        start = normalize_date(period.get("start"))
        end = normalize_date(period.get("end"))
        if start and end and start > end:
            drafts.append(_draft(
                ctx, finding_type="INVALID_DATE_ORDER",
                title=f"{doc.document_name or doc.document_id}: effective start {start} after end {end}",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                severity=Severity.LOW,
                evidence_ids=[],
                possible_consequence="Period alignment for this document is unreliable",
                recommended_action="Correct the effective period metadata",
                deduplication_key=f"DQ-DATE:{doc.document_id}"))
    for claim in case.claims:
        if claim.loss_date and claim.valuation_date and claim.loss_date > claim.valuation_date:
            drafts.append(_draft(
                ctx, finding_type="INVALID_DATE_ORDER",
                title=f"Claim {claim.claim_id}: loss date {claim.loss_date} after valuation "
                      f"date {claim.valuation_date}",
                classification=FindingClassification.NON_MANIFESTED_DISCOVERY,
                conclusion_state=ConclusionState.CONFIRMED_DATA_CONFLICT,
                severity=Severity.LOW,
                evidence_ids=_ev(claim, "claim_id", "loss_date"),
                possible_consequence="Loss record timing is inconsistent",
                recommended_action="Verify loss and valuation dates",
                deduplication_key=f"DQ-DATE:claim:{claim.claim_id}"))
    return drafts


def dq_stale_documents(ctx: RuleContext) -> List[FindingDraft]:
    case = ctx.case
    if ctx.profile is None:
        return []
    required = ctx.profile.required_types(case.line_of_business)
    review = normalize_date(case.review_date) or _today().isoformat()
    drafts: List[FindingDraft] = []
    for doc in case.documents:
        req = required.get(doc.document_type)
        if req is None or req.freshness_days is None or not doc.received_at:
            continue
        received = normalize_date(doc.received_at)
        if not received:
            continue
        age = (date.fromisoformat(review) - date.fromisoformat(received)).days
        if age <= req.freshness_days:
            continue
        drafts.append(_draft(
            ctx, finding_type="STALE_DOCUMENT",
            title=f"{doc.document_name or doc.document_id} is {age} days old "
                  f"(freshness limit {req.freshness_days})",
            classification=FindingClassification.EVIDENCE_GAP,
            conclusion_state=ConclusionState.EVIDENCE_GAP,
            severity=Severity.LOW,
            materiality_band=MaterialityBand.LOW,
            materiality_rationale="Stale evidence may no longer reflect current facts",
            evidence_ids=[],
            uncertainty_reason="Staleness is a freshness signal, not evidence the data is wrong",
            possible_consequence="Review may rely on outdated values",
            recommended_action=f"Request a current {doc.document_type}",
            deduplication_key=f"DQ-STALE:{doc.document_id}"))
    return drafts


def all_handlers() -> Dict[str, object]:
    return {
        "doc_inventory": doc_inventory,
        "p1_driver_roster": p1_driver_roster,
        "p2_location_reconciliation": p2_location_reconciliation,
        "p3_fleet_count": p3_fleet_count,
        "p3_wc_payroll": p3_wc_payroll,
        "p4_version_discrepancy": p4_version_discrepancy,
        "p5_claims_reconciliation": p5_claims_reconciliation,
        "p6_experience_mod": p6_experience_mod,
        "p7_operations_context": p7_operations_context,
        "p11_exposure_vs_limit": p11_exposure_vs_limit,
        "n3_wc_state_allocation": n3_wc_state_allocation,
        "n4_employee_counts": n4_employee_counts,
        "n5_annual_claim_counts": n5_annual_claim_counts,
        "n6_gl_bases": n6_gl_bases,
        "n7_garaging_state": n7_garaging_state,
        "n8_driver_qualification": n8_driver_qualification,
        "n14_external_record_match": n14_external_record_match,
        "n15_class_rate": n15_class_rate,
        "dq_duplicate_ids": dq_duplicate_ids,
        "dq_arithmetic": dq_arithmetic,
        "dq_date_order": dq_date_order,
        "dq_stale_documents": dq_stale_documents,
    }
