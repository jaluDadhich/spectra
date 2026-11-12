"""Header-concept mappings for cell-grid workbook tables.

Each concept maps generic spreadsheet header vocabulary (domain language,
never account-specific values) onto canonical record fields. Classification
is scored: a table matches a concept only when its key field is found and
enough fields match, so unrelated sheets are preserved as context instead
of being force-fit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.extraction.cellgrid import GridTable


@dataclass(frozen=True)
class ConceptField:
    name: str
    aliases: Tuple[str, ...]
    kind: str = "str"  # str | number | money | date | state | int


@dataclass(frozen=True)
class Concept:
    name: str
    document_type: str
    key_fields: Tuple[str, ...]
    fields: Tuple[ConceptField, ...]
    min_score: int = 3


CONCEPTS: Tuple[Concept, ...] = (
    Concept(
        name="loss_detail", document_type="loss_run",
        key_fields=("claim_id",),
        fields=(
            ConceptField("claim_id", ("claim id", "claim number", "claim no")),
            ConceptField("policy_year", ("policy year", "policy yr")),
            ConceptField("loss_date", ("loss date", "date of loss"), "date"),
            ConceptField("report_date", ("report date", "date reported"), "date"),
            ConceptField("line_of_business", ("line of business", "lob", "coverage line")),
            ConceptField("state", ("state",), "state"),
            ConceptField("location_id", ("location id", "location")),
            ConceptField("cause", ("cause category", "cause")),
            ConceptField("paid_indemnity", ("paid indemnity", "indemnity paid", "indemnity"), "money"),
            ConceptField("paid_expense", ("paid expense", "expense paid", "alae", "loss expense"), "money"),
            ConceptField("paid", ("total paid", "paid amount", "paid"), "money"),
            ConceptField("reserve_indemnity", ("reserve indemnity", "indemnity reserve"), "money"),
            ConceptField("reserve_expense", ("reserve expense", "expense reserve", "alae reserve"), "money"),
            ConceptField("reserves", ("case reserve", "outstanding reserve", "total reserves", "reserves", "reserve", "outstanding"), "money"),
            ConceptField("incurred", ("reported incurred", "total incurred", "incurred"), "money"),
            ConceptField("calculated_incurred", ("calculated incurred", "recalculated incurred"), "money"),
            ConceptField("incurred_variance", ("incurred variance", "variance"), "money"),
            ConceptField("status", ("status", "claim status")),
        ),
        min_score=4,
    ),
    Concept(
        name="fleet_schedule", document_type="fleet_schedule",
        key_fields=("unit_id",),
        fields=(
            ConceptField("unit_id", ("unit id", "unit number", "unit no", "vehicle id")),
            ConceptField("vin", ("vin",)),
            ConceptField("model_year", ("model year", "year")),
            ConceptField("vehicle_type", ("vehicle type", "body type", "unit type")),
            ConceptField("gvwr", ("gvwr", "gvw", "gross vehicle weight"), "number"),
            ConceptField("status", ("status", "unit status")),
            ConceptField("garaging_state", ("garaging state", "garage state", "garaging st"), "state"),
            ConceptField("garaging_address", ("garaging address", "garage address", "garaging location")),
            ConceptField("assigned_driver_id", ("assigned driver", "primary driver")),
            ConceptField("cost_new", ("cost new",), "money"),
        ),
        min_score=4,
    ),
    Concept(
        name="driver_roster", document_type="driver_roster",
        key_fields=("driver_id",),
        fields=(
            ConceptField("driver_id", ("driver id", "driver number", "driver no")),
            ConceptField("name", ("driver name", "name")),
            ConceptField("license_state", ("license state", "licence state", "license st"), "state"),
            ConceptField("license_class", ("license class", "licence class", "cdl class", "license type")),
            ConceptField("cdl_endorsements", ("endorsements", "endorsement")),
            ConceptField("hire_date", ("hire date", "date hired"), "date"),
            ConceptField("mvr_review_date", ("mvr last reviewed", "mvr review date", "mvr review", "mvr date", "mvr"), "date"),
            ConceptField("status", ("status", "driver status")),
            ConceptField("assigned_unit_id", ("assigned unit", "assigned vehicle")),
        ),
        min_score=4,
    ),
    Concept(
        name="sov", document_type="sov",
        key_fields=("location_id", "address"),
        fields=(
            ConceptField("location_id", ("location id", "loc id", "location")),
            ConceptField("premises_number", ("premises #", "premises number", "premises no")),
            ConceptField("address", ("street address", "site address", "address")),
            ConceptField("city", ("city",)),
            ConceptField("state", ("state",), "state"),
            ConceptField("zip", ("zip code", "zip", "postal")),
            ConceptField("occupancy", ("occupancy", "use / description", "use")),
            ConceptField("square_footage", ("square footage", "sqft", "sq ft", "area"), "number"),
            ConceptField("building_value", ("building value", "building"), "money"),
            ConceptField("contents_value", ("business personal property", "bpp", "contents value", "contents"), "money"),
            ConceptField("business_income", ("business income",), "money"),
            ConceptField("tiv", ("total insured value", "tiv", "total value"), "money"),
        ),
        min_score=4,
    ),
    Concept(
        name="wc_payroll", document_type="wc_rating_workbook",
        key_fields=("class_code",),
        fields=(
            ConceptField("location_id", ("location id", "location", "loc")),
            ConceptField("state", ("state",), "state"),
            ConceptField("class_code", ("class code", "class")),
            ConceptField("description", ("category / duties", "category", "duties", "description")),
            ConceptField("full_time", ("full-time employees", "full time employees", "full-time", "full time", "ft employees"), "number"),
            ConceptField("part_time", ("part-time employees", "part time employees", "part-time", "part time", "pt employees"), "number"),
            ConceptField("payroll", ("estimated annual payroll", "estimated payroll", "annual payroll", "payroll"), "money"),
            ConceptField("rate", ("rate per $100", "rate per 100", "rate"), "number"),
            ConceptField("manual_premium", ("manual premium",), "money"),
            ConceptField("basis", ("payroll basis", "basis")),
        ),
        min_score=3,
    ),
    Concept(
        name="emod_history", document_type="wc_rating_workbook",
        key_fields=("term", "factor"),
        fields=(
            ConceptField("term", ("policy term", "term", "period")),
            ConceptField("factor", ("mod factor", "experience mod", "mod", "factor"), "number"),
            ConceptField("effective_date", ("effective date", "eff date"), "date"),
            ConceptField("source", ("source document", "source")),
            ConceptField("currency_of_term", ("current / prior", "current/prior", "term type", "status")),
        ),
        min_score=3,
    ),
    Concept(
        name="checklist", document_type="submission_checklist",
        key_fields=("item",),
        fields=(
            ConceptField("item", ("required document", "required item", "item", "document", "requirement")),
            ConceptField("expected_format", ("expected format", "format")),
            ConceptField("status", ("status", "state")),
            ConceptField("note", ("why / test note", "why", "note", "comment")),
        ),
        min_score=2,
    ),
    Concept(
        name="external_records", document_type="external_records",
        key_fields=("record_type", "entity_name", "summary"),
        fields=(
            ConceptField("record_type", ("record type", "source category", "category", "type")),
            ConceptField("entity_name", ("employer / entity string", "employer", "entity / topic", "entity", "topic")),
            ConceptField("summary", ("record summary", "observed public fact", "summary", "fact")),
            ConceptField("address", ("address / scope", "address", "site", "scope")),
            ConceptField("url", ("official source url", "source url", "url", "link")),
            ConceptField("status", ("status displayed", "status")),
            ConceptField("retrieved", ("retrieval date", "retrieved", "date"), "date"),
            ConceptField("caveat", ("exact match caveat", "caveat", "limitation", "underwriting use")),
        ),
        min_score=2,
    ),
    Concept(
        name="kv_measures", document_type="",  # contributes exposures only
        key_fields=("measure", "value"),
        fields=(
            ConceptField("measure", ("measure", "metric", "item", "input")),
            ConceptField("value", ("application / narrative", "application", "declared", "value")),
        ),
        min_score=2,
    ),
)

# Generic measure-name aliases for key/value sheets. Maps a normalized
# measure label to (exposure_type, unit). Values are domain vocabulary only.
MEASURE_ALIASES: Dict[str, Tuple[str, str]] = {
    "declared power units": ("vehicle_count", "count"),
    "declared vehicles": ("vehicle_count", "count"),
    "power units": ("vehicle_count", "count"),
    "vehicle count": ("vehicle_count", "count"),
    "gross sales": ("sales", "USD"),
    "annual gross sales": ("sales", "USD"),
    "total sales": ("sales", "USD"),
    "total payroll": ("declared_payroll_total", "USD"),
    "wc payroll": ("declared_payroll_total", "USD"),
    "estimated payroll": ("declared_payroll_total", "USD"),
    "schedule payroll target": ("declared_payroll_total", "USD"),
    "total incurred": ("loss_summary_incurred", "USD"),
    "carrier summary reported incurred": ("loss_summary_incurred", "USD"),
    "carrier reported incurred": ("loss_summary_incurred", "USD"),
    "summary total incurred": ("loss_summary_incurred", "USD"),
    "claim detail rows": ("loss_summary_count", "count"),
    "total claims": ("loss_summary_count", "count"),
    "claim count": ("loss_summary_count", "count"),
    "number of claims": ("loss_summary_count", "count"),
}

_NORMALIZE_RE = re.compile(r"[^a-z0-9$]+")


def normalize_header(header: str) -> str:
    return _NORMALIZE_RE.sub(" ", header.lower()).strip()


def _alias_score(header_norm: str, alias: str) -> int:
    if header_norm == alias:
        return 3
    if header_norm.startswith(alias + " ") or header_norm.endswith(" " + alias):
        return 2
    if alias in header_norm:
        return 1
    return 0


def match_fields(table: GridTable, concept: Concept) -> Dict[str, str]:
    """Match table headers to concept fields. Returns field_name -> header.

    Greedy by (score, alias length) with each header and each field used at
    most once, so specific fields (e.g. paid_indemnity) win over generic
    ones (e.g. paid).
    """
    headers_norm = {h: normalize_header(h) for h in table.headers}
    candidates: List[Tuple[int, int, str, str]] = []
    for fld in concept.fields:
        for header, norm in headers_norm.items():
            best = max((_alias_score(norm, a) for a in fld.aliases), default=0)
            if best:
                longest = max(len(a) for a in fld.aliases if _alias_score(norm, a) == best)
                candidates.append((best, longest, fld.name, header))
    candidates.sort(key=lambda c: (-c[0], -c[1]))
    assigned_fields: Dict[str, str] = {}
    used_headers: set = set()
    for score, _, fname, header in candidates:
        if fname in assigned_fields or header in used_headers:
            continue
        assigned_fields[fname] = header
        used_headers.add(header)
    return assigned_fields


def classify_table(table: GridTable) -> Optional[Tuple[Concept, Dict[str, str]]]:
    """Return the best matching (concept, field->header map) or None."""
    best: Optional[Tuple[int, Concept, Dict[str, str]]] = None
    for concept in CONCEPTS:
        mapping = match_fields(table, concept)
        if not any(k in mapping for k in concept.key_fields):
            continue
        score = len(mapping)
        if score < concept.min_score:
            continue
        if best is None or score > best[0]:
            best = (score, concept, mapping)
    if best is None:
        return None
    return best[1], best[2]


_DOC_TYPE_PRECEDENCE = (
    "loss_run", "fleet_schedule", "driver_roster", "sov",
    "wc_rating_workbook", "external_records", "submission_checklist",
)


def resolve_document_type(concepts: List[Concept]) -> Optional[str]:
    types = {c.document_type for c in concepts if c.document_type}
    for doc_type in _DOC_TYPE_PRECEDENCE:
        if doc_type in types:
            return doc_type
    return None
