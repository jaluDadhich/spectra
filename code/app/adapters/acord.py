"""Adapters for ACORD 125 / 126 / 130 / 140 JSON documents.

Field naming in the input JSON is alias-tolerant; see docs/schemas.md for
the documented input shapes. Raw strings are preserved on evidence items.
"""
from __future__ import annotations

import re

from app.adapters.base import (
    AdaptedDocument,
    DocumentAdapter,
    EvidenceFactory,
    as_list,
    get_any,
)
from app.normalization import (
    normalize_class_code,
    normalize_date,
    normalize_entity_name,
    normalize_money,
    normalize_state,
    parse_number,
)
from app.schemas.canonical import (
    EntityRecord,
    ExposureRecord,
    LocationRecord,
    RatingFactorRecord,
    WCPayrollRow,
)
from app.schemas.envelope import InputEnvelope


_STRUCTURED_NUMBER_PREFIX = re.compile(
    r"^\s*[$€£]?\s*\(?\s*(-?\d[\d,]*(?:\.\d+)?)")


def _parse_structured_number(value: Any) -> Optional[float]:
    """Parse a numeric field that carries a trailing source annotation.

    Extracted ACORD fields can include labels such as ``(synthetic)`` or an
    em-dash qualification after the actual value. Only use prefix parsing in
    a field already identified as numeric; never scan arbitrary prose.
    """
    parsed = parse_number(value)
    if parsed is not None or not isinstance(value, str):
        return parsed
    match = _STRUCTURED_NUMBER_PREFIX.match(value)
    return parse_number(match.group(1)) if match else None


def _normalize_structured_money(value: Any) -> tuple[Optional[float], Optional[str]]:
    amount = _parse_structured_number(value)
    if amount is None:
        return None, None
    currency = "EUR" if isinstance(value, str) and "€" in value else (
        "GBP" if isinstance(value, str) and "£" in value else "USD")
    return amount, currency


def _pick(entry: dict, *keys: str):
    """Alias-tolerant lookup returning (actual_key, value) so evidence paths
    cite the key that really exists in the source JSON."""
    for key in keys:
        if key in entry and entry[key] is not None:
            return key, entry[key]
    lowered = {k.lower(): k for k in entry}
    for key in keys:
        actual = lowered.get(key.lower())
        if actual is not None and entry[actual] is not None:
            return actual, entry[actual]
    return None, None


def _location_from(entry: dict, idx: int, doc_id: str, ev: EvidenceFactory, path: str) -> LocationRecord:
    loc_id = (get_any(entry, "location_id", "loc_id", "id", "location_number", "location")
              or f"{doc_id}#loc{idx}")
    state = get_any(entry, "state", "state_code")
    sqft = _parse_structured_number(get_any(entry, "square_footage", "sqft", "area"))
    bldg = parse_number(get_any(entry, "building_value", "building"))
    contents = parse_number(get_any(entry, "contents_value", "contents",
                                    "business_personal_property_value"))
    tiv = parse_number(get_any(entry, "tiv", "total_insured_value"))
    if tiv is None and (bldg is not None or contents is not None):
        tiv = (bldg or 0.0) + (contents or 0.0)
    field_ev: dict[str, str] = {}
    location_key, location_value = _pick(
        entry, "location_id", "loc_id", "id", "location_number", "location")
    if location_key is not None:
        field_ev["location_id"] = ev.make(
            f"{path}.{location_key}", f"locations[{loc_id}].location_id",
            location_value)
    for field, keys, norm in (
            ("address", ("address", "street", "street_address", "site_address"), None),
            ("state", ("state", "state_code"), "state"),
            ("building_value", ("building_value", "building"), None),
            ("contents_value", ("contents_value", "contents",
                                "business_personal_property_value"), None),
            ("tiv", ("tiv", "total_insured_value"), None),
            ("square_footage", ("square_footage", "sqft", "area"), None)):
        actual_key, value = _pick(entry, *keys)
        if actual_key is None:
            continue
        field_ev[field] = ev.make(
            f"{path}.{actual_key}", f"locations[{loc_id}].{field}", value,
            normalized_value=normalize_state(value) if norm == "state" else None)
    return LocationRecord(
        record_id=str(loc_id), document_id=doc_id, field_evidence=field_ev,
        location_id=str(loc_id),
        address=get_any(entry, "address", "street", "street_address", "site_address"),
        city=get_any(entry, "city"), state=normalize_state(state),
        postal_code=get_any(entry, "zip", "postal_code"),
        building_value=bldg, contents_value=contents, tiv=tiv, square_footage=sqft,
    )


class Acord125Adapter(DocumentAdapter):
    document_type = "acord_125"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        applicant_key, applicant = _pick(
            data, "applicant", "named_insured", "insured", "applicant_operations")
        applicant = applicant or {}
        if isinstance(applicant, str):
            applicant = {"legal_name": applicant}
        legal_key, legal = _pick(applicant, "legal_name", "name", "named_insured")
        fe = {}
        if legal is not None:
            fe["legal_name"] = ev.make(f"{applicant_key}.{legal_key}",
                                       "entities[insured].legal_name", legal,
                                       normalized_value=normalize_entity_name(legal))
        fein_key, fein = _pick(applicant, "fein", "tax_id")
        if fein is not None:
            fe["fein"] = ev.make(f"{applicant_key}.{fein_key}", "entities[insured].fein", fein)
        out.entities.append(EntityRecord(
            record_id=f"{doc_id}#insured", document_id=doc_id, field_evidence=fe,
            legal_name=legal, normalized_name=normalize_entity_name(legal),
            dba=[str(d) for d in as_list(get_any(applicant, "dba", "dbas"))],
            parent_name=get_any(applicant, "parent", "parent_company"),
            subsidiaries=[str(s) for s in as_list(get_any(applicant, "subsidiaries"))],
            fein=str(fein) if fein is not None else None,
            usdot_number=(str(u) if (u := get_any(applicant, "usdot_number", "usdot")) is not None else None),
        ))
        locations_key, locations_value = _pick(data, "locations", "premises", "declared_premises")
        locations = as_list(locations_value)
        for i, entry in enumerate(locations):
            if isinstance(entry, dict):
                path = f"{locations_key}[{i}]" if locations_key else f"locations[{i}]"
                out.locations.append(_location_from(entry, i, doc_id, ev, path))
        ops_block_key, ops_block = _pick(
            data, "business_operations", "operations_section", "applicant_operations")
        ops_block = ops_block or {}
        sales = get_any(data, "gross_sales", "annual_sales", "total_sales")
        sales_path = None
        if sales is None and isinstance(ops_block, dict):
            key, sales = _pick(ops_block, "annual_gross_sales", "gross_sales", "total_sales")
            if key:
                sales_path = f"{ops_block_key}.{key}"
        if sales is not None:
            amount, currency = _normalize_structured_money(sales)
            period = get_any(data, "sales_period") or (
                get_any(ops_block, "sales_period") if isinstance(ops_block, dict) else None) or "annual"
            eid = ev.make(sales_path or "gross_sales", "exposures[sales].value", sales,
                          normalized_value=amount, unit=currency, period=period)
            out.exposures.append(ExposureRecord(
                record_id=f"{doc_id}#sales", document_id=doc_id, field_evidence={"value": eid},
                exposure_type="sales", value=amount, unit=currency, period=period))
        counts = get_any(data, "employee_counts", "employees", default={}) or {}
        counts_derived = False
        if not counts and locations:
            # Derive application-level counts from per-location counts when
            # the form carries them on the location schedule instead.
            ft_vals = [v for v in (parse_number(get_any(loc, "full_time_employees", "full_time"))
                                   for loc in locations if isinstance(loc, dict))
                       if v is not None]
            pt_vals = [v for v in (parse_number(get_any(loc, "part_time_employees", "part_time"))
                                   for loc in locations if isinstance(loc, dict))
                       if v is not None]
            counts = {}
            if ft_vals:
                counts["full_time"] = sum(ft_vals)
            if pt_vals:
                counts["part_time"] = sum(pt_vals)
            counts_derived = bool(counts)
        if isinstance(counts, dict):
            for kind, keys in (("full_time", ("full_time", "ft")), ("part_time", ("part_time", "pt"))):
                raw = get_any(counts, *keys)
                if raw is not None:
                    if counts_derived:
                        eid = ev.make(
                        locations_key or "locations", f"exposures[employees].{kind}", raw,
                            normalized_value=parse_number(raw),
                            snippet=f"derived: sum of per-location {kind}_employees")
                    else:
                        eid = ev.make(f"employee_counts.{kind}", f"exposures[employees].{kind}", raw,
                                      normalized_value=parse_number(raw))
                    out.exposures.append(ExposureRecord(
                        record_id=f"{doc_id}#emp_{kind}", document_id=doc_id,
                        field_evidence={"value": eid}, exposure_type=f"employees_{kind}",
                        value=parse_number(raw), unit="count"))
        ops = get_any(data, "operations_description", "operations", "business_description")
        ops_path = "operations_description"
        if ops is None and isinstance(ops_block, dict):
            key, ops = _pick(ops_block, "nature_of_business", "operations_description",
                             "primary_operations",
                             "description_of_operations", "description")
            if key:
                ops_path = f"{ops_block_key}.{key}"
        if ops is not None:
            eid = ev.make(ops_path, "narratives[operations].text", ops)
            from app.schemas.canonical import NarrativeRecord
            out.narratives.append(NarrativeRecord(
                record_id=f"{doc_id}#ops", document_id=doc_id,
                field_evidence={"text": eid}, text=str(ops), subject="operations"))
        if isinstance(ops_block, dict):
            power_key, raw_power = _pick(ops_block, "active_power_units", "power_units",
                                         "vehicle_count")
            power_count = _parse_structured_number(raw_power)
            if power_count is not None:
                path = f"{ops_block_key}.{power_key}"
                eid = ev.make(path, "exposures[vehicle_count].value", raw_power,
                              normalized_value=power_count, unit="count")
                out.exposures.append(ExposureRecord(
                    record_id=f"{doc_id}#vehicle_count", document_id=doc_id,
                    field_evidence={"value": eid}, exposure_type="vehicle_count",
                    value=power_count, unit="count"))


class Acord126Adapter(DocumentAdapter):
    document_type = "acord_126"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        # GL schedule of hazards: split rows by rating basis (sales vs area).
        hazards_key, hazards = _pick(
            data, "schedule_of_hazards", "hazards", "gl_schedule",
            "general_liability_rating_exposure_schedule")
        for i, row in enumerate(as_list(hazards)):
            if not isinstance(row, dict):
                continue
            basis = str(get_any(row, "basis", "rating_basis", "exposure_basis", default="")).lower()
            exposure = get_any(row, "exposure", "amount", "value")
            if exposure is None:
                continue
            rid = str(get_any(row, "row_id", "id") or f"{doc_id}#hazard{i}")
            location_id = get_any(row, "location_id", "location")
            value_key, raw_exposure = _pick(row, "exposure", "amount", "value")
            if "area" in basis or "square" in basis:
                sqft = _parse_structured_number(raw_exposure)
                eid = ev.make(f"{hazards_key}[{i}].{value_key}", f"exposures[{rid}].value",
                              raw_exposure, normalized_value=sqft, unit="sqft")
                out.exposures.append(ExposureRecord(
                    record_id=rid, document_id=doc_id, field_evidence={"value": eid},
                    exposure_type="area_rated", value=sqft, unit="sqft",
                    location_id=(str(location_id) if location_id is not None else None),
                    description=str(get_any(row, "classification_description", "description",
                                            default=""))))
            elif "sales" in basis or "revenue" in basis or "gross" in basis:
                amount, currency = normalize_money(raw_exposure)
                eid = ev.make(f"{hazards_key}[{i}].{value_key}", f"exposures[{rid}].value",
                              raw_exposure, normalized_value=amount, unit=currency)
                # A reported total/control row is useful evidence but must not
                # be added again to the sales-rated class allocations.
                total_control = "total" in basis or basis.strip() == "annual gross sales"
                out.exposures.append(ExposureRecord(
                    record_id=rid, document_id=doc_id, field_evidence={"value": eid},
                    exposure_type=("sales_rated_control" if total_control else "sales_rated"),
                    value=amount, unit=currency,
                    location_id=(str(location_id) if location_id is not None else None),
                    description=str(get_any(row, "classification_description", "description",
                                            "exposure_basis", "basis", default=""))))
        for i, row in enumerate(as_list(get_any(data, "sales_rows", "sales_rated_exposures"))):
            if not isinstance(row, dict):
                continue
            amount, currency = normalize_money(get_any(row, "amount", "sales", "value"))
            rid = str(get_any(row, "row_id", "id") or f"{doc_id}#sales{i}")
            fe = {}
            if amount is not None:
                fe["value"] = ev.make(f"sales_rows[{i}].amount", f"exposures[{rid}].value",
                                      get_any(row, "amount", "sales", "value"),
                                      normalized_value=amount, unit=currency)
            out.exposures.append(ExposureRecord(
                record_id=rid, document_id=doc_id, field_evidence=fe,
                exposure_type="sales_rated", value=amount, unit=currency,
                location_id=(str(l) if (l := get_any(row, "location_id")) is not None else None),
                description=get_any(row, "description", "basis")))
        for i, row in enumerate(as_list(get_any(data, "area_rows", "area_rated_exposures"))):
            if not isinstance(row, dict):
                continue
            sqft = parse_number(get_any(row, "square_footage", "sqft", "area"))
            rid = str(get_any(row, "row_id", "id") or f"{doc_id}#area{i}")
            fe = {}
            if sqft is not None:
                fe["value"] = ev.make(f"area_rows[{i}].square_footage", f"exposures[{rid}].value",
                                      get_any(row, "square_footage", "sqft", "area"),
                                      normalized_value=sqft, unit="sqft")
            out.exposures.append(ExposureRecord(
                record_id=rid, document_id=doc_id, field_evidence=fe,
                exposure_type="area_rated", value=sqft, unit="sqft",
                location_id=(str(l) if (l := get_any(row, "location_id")) is not None else None),
                description=get_any(row, "description")))
        history_key, history_entries = _pick(
            data, "claims_history", "loss_history", "prior_insurance_gl_loss_summary",
            "claims_experience_annual_counts")
        for i, entry in enumerate(as_list(history_entries)):
            if not isinstance(entry, dict):
                continue
            if history_key == "claims_experience_annual_counts":
                year = get_any(entry, "policy_year", "year")
                count_fields = [("loss_detail_count", "all lines"),
                                ("gl", "general liability"),
                                ("wc", "workers compensation"),
                                ("auto", "commercial auto"),
                                ("property", "property"),
                                ("cargo", "motor truck cargo")]
                for count_key, scope in count_fields:
                    raw_count = get_any(entry, count_key)
                    count = parse_number(raw_count)
                    if count is None or year is None:
                        continue
                    rid = f"{doc_id}#hist{i}#{scope.replace(' ', '_')}"
                    eid = ev.make(f"{history_key}[{i}].{count_key}",
                                  f"claims_history[{year}].{scope}.count", raw_count,
                                  normalized_value=count, period=str(year))
                    out.exposures.append(ExposureRecord(
                        record_id=rid, document_id=doc_id, field_evidence={"count": eid},
                        exposure_type="annual_claim_count", value=count, unit="count",
                        period=str(year), description=scope))
                continue
            count_key, raw_count = _pick(
                entry, "claim_count", "count", "number_of_claims", "claim_count_all_lines",
                "loss_detail_count")
            count = parse_number(raw_count)
            year = get_any(entry, "year", "policy_year")
            rid = f"{doc_id}#hist{i}"
            fe = {}
            if count is not None:
                fe["count"] = ev.make(f"{history_key}[{i}].{count_key}",
                                      f"claims_history[{year}].count",
                                      raw_count,
                                      normalized_value=count, period=str(year))
            line_of_business = get_any(entry, "line_of_business", "lob")
            all_lines = (
                count_key in {"claim_count_all_lines", "loss_detail_count"}
                or bool(get_any(entry, "all_lines", "all_line_count", default=False))
            )
            if line_of_business is None and history_key == "prior_insurance_gl_loss_summary" \
                    and not all_lines:
                line_of_business = "GL"
            scope = ("all lines" if all_lines else
                     str(line_of_business) if line_of_business is not None else "unspecified")
            out.exposures.append(ExposureRecord(
                record_id=rid, document_id=doc_id, field_evidence=fe,
                exposure_type="annual_claim_count", value=count, unit="count",
                period=str(year) if year is not None else None,
                description=scope))


class Acord130Adapter(DocumentAdapter):
    document_type = "acord_130"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        rows_key, rows_data = _pick(
            data, "class_rows", "classifications", "wc_class_rows",
            "state_rating_worksheet",
            "state_rating_worksheet_estimated_payroll_by_classification",
            "payroll_by_location_and_classification")
        for i, row in enumerate(as_list(rows_data)):
            if not isinstance(row, dict):
                continue
            rid = str(get_any(row, "row_id", "id") or f"{doc_id}#wc{i}")
            payroll, currency = normalize_money(get_any(row, "payroll", "estimated_payroll"))
            rate = parse_number(get_any(row, "rate", "class_rate", "rate_per_100"))
            fe = {}
            for field, aliases, norm in (
                ("location_id", ("location_id", "location"), None),
                ("class_code", ("class_code", "class"), None),
                ("state", ("state", "state_code"), "state"),
                ("payroll", ("payroll", "estimated_payroll"), "money"),
                ("rate", ("rate", "class_rate", "rate_per_100"), "number"),
                ("full_time", ("full_time", "ft_employees", "full_time_employees"), "number"),
                ("part_time", ("part_time", "pt_employees", "part_time_employees"), "number"),
            ):
                actual_key, value = _pick(row, *aliases)
                if value is None:
                    continue
                normalized = (normalize_state(value) if norm == "state" else
                              normalize_class_code(value) if field == "class_code" else
                              payroll if field == "payroll" else
                              rate if field == "rate" else parse_number(value))
                fe[field] = ev.make(f"{rows_key}[{i}].{actual_key}",
                                    f"wc_rows[{rid}].{field}", value,
                                    normalized_value=normalized,
                                    unit=currency if field == "payroll" else None)
            state = normalize_state(get_any(row, "state", "state_code"))
            class_code = normalize_class_code(get_any(row, "class_code", "class"))
            out.wc_payroll_rows.append(WCPayrollRow(
                record_id=rid, document_id=doc_id, field_evidence=fe,
                class_code=class_code,
                state=state,
                location_id=(str(l) if (l := get_any(row, "location_id", "location")) is not None else None),
                payroll=payroll, rate=rate,
                full_time_count=(int(v) if (v := parse_number(get_any(row, "full_time", "ft_employees", "full_time_employees"))) is not None else None),
                part_time_count=(int(v) if (v := parse_number(get_any(row, "part_time", "pt_employees", "part_time_employees"))) is not None else None)))
            if rate is not None and class_code and state:
                out.rating_factors.append(RatingFactorRecord(
                    record_id=f"{rid}#rate", document_id=doc_id,
                    field_evidence={"value": fe["rate"]} if "rate" in fe else {},
                    factor_type="class_rate", value=rate, state=state,
                    class_code=class_code, source_authority="acord_130"))
        terms_key, terms = _pick(data, "workers_compensation_terms", "wc_terms")
        declared_key, declared = _pick(data, "declared_payroll_total", "total_payroll",
                                       "payroll_declared_on_application")
        if declared is None and isinstance(terms, dict):
            nested_key, declared = _pick(terms, "estimated_payroll", "total_payroll",
                                         "declared_payroll")
            if nested_key:
                declared_key = f"{terms_key}.{nested_key}"
        if declared is not None:
            amount, _ = _normalize_structured_money(declared)
            if amount is not None:
                eid = ev.make(declared_key or "declared_payroll_total",
                              "wc_application.total_payroll",
                              declared, normalized_value=amount, unit="USD")
                out.exposures.append(ExposureRecord(
                    record_id=f"{doc_id}#declared_payroll", document_id=doc_id,
                    field_evidence={"value": eid}, exposure_type="declared_payroll_total",
                    value=amount, unit="USD"))
        history_key, history_rows = _pick(
            data, "claims_history", "loss_history",
            "prior_carrier_information_loss_history", "wc_claims_history")
        for i, entry in enumerate(as_list(history_rows)):
            if not isinstance(entry, dict):
                continue
            count_key, raw_count = _pick(entry, "claim_count", "count", "number_of_claims",
                                         "wc_claim_count")
            count = parse_number(raw_count)
            year = get_any(entry, "year", "policy_year")
            if count is None or year is None:
                continue
            eid = ev.make(f"{history_key}[{i}].{count_key}",
                          f"claims_history[{year}].count",
                          raw_count,
                          normalized_value=count, period=str(year))
            out.exposures.append(ExposureRecord(
                record_id=f"{doc_id}#hist{i}", document_id=doc_id,
                field_evidence={"count": eid}, exposure_type="annual_claim_count",
                value=count, unit="count", period=str(year),
                description=str(get_any(entry, "line_of_business", "lob", default="WC"))))
        emod_key, emod = _pick(data, "experience_mod", "experience_modification",
                               "experience_modification_premium_calculation",
                               "experience_modifier")
        emod_is_scalar = not isinstance(emod, dict) and emod is not None
        if emod_is_scalar:
            emod = {"factor": emod}
        if isinstance(emod, dict):
            factor_key, raw_factor = _pick(emod, "factor", "value", "mod",
                                           "experience_modification_shown_on_application")
            factor = _parse_structured_number(raw_factor)
            eff_key, raw_eff = _pick(emod, "effective_date", "anniversary_date")
            if raw_eff is None and envelope.document.effective_period:
                raw_eff = envelope.document.effective_period.start
            eff = normalize_date(raw_eff)
            fe = {}
            if factor is not None:
                fe["value"] = ev.make(
                    (emod_key if emod_is_scalar else f"{emod_key}.{factor_key}"),
                    "rating_factors[experience_mod].value",
                    raw_factor, normalized_value=factor, period=eff)
            out.rating_factors.append(RatingFactorRecord(
                record_id=f"{doc_id}#emod", document_id=doc_id, field_evidence=fe,
                factor_type="experience_mod", value=factor, effective_date=eff,
                source_authority="acord_130"))
            # When the form also states the factor applied in the draft
            # quote, keep it as a distinct quote-sourced factor so U04 can
            # compare application vs quote.
            quote_key, raw_quote_factor = _pick(emod, "factor_used_in_draft_quote",
                                                "quote_factor")
            quote_factor = parse_number(raw_quote_factor)
            if quote_factor is not None:
                qfe = {"value": ev.make(
                    f"{emod_key}.{quote_key}", "rating_factors[quote_experience_mod].value",
                    raw_quote_factor, normalized_value=quote_factor, period=eff)}
                out.rating_factors.append(RatingFactorRecord(
                    record_id=f"{doc_id}#emod_quote", document_id=doc_id, field_evidence=qfe,
                    factor_type="experience_mod", value=quote_factor, effective_date=eff,
                    source_authority="quote"))
            payroll_key, raw_declared = _pick(emod, "payroll_declared_on_application",
                                              "declared_payroll")
            declared_amount, _ = normalize_money(raw_declared)
            if declared_amount is not None:
                eid = ev.make(f"{emod_key}.{payroll_key}", "wc_application.total_payroll",
                              raw_declared, normalized_value=declared_amount, unit="USD")
                out.exposures.append(ExposureRecord(
                    record_id=f"{doc_id}#declared_payroll", document_id=doc_id,
                    field_evidence={"value": eid}, exposure_type="declared_payroll_total",
                    value=declared_amount, unit="USD"))


class Acord140Adapter(DocumentAdapter):
    document_type = "acord_140"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        # Subject-of-insurance amounts attach to the first (main) premises.
        subjects_key, subjects = _pick(data, "subject_of_insurance_coverage_requested",
                                       "subjects_of_insurance")
        subject_amounts: dict[str, tuple[str, Any]] = {}
        for s_i, subj in enumerate(as_list(subjects)):
            if not isinstance(subj, dict):
                continue
            label = str(get_any(subj, "subject", "name", default="")).lower()
            amount_key, amount = _pick(subj, "amount_requested", "amount", "value")
            if amount is None:
                continue
            if "building" in label and "building_value" not in subject_amounts:
                subject_amounts["building_value"] = (f"{subjects_key}[{s_i}].{amount_key}", amount)
            elif ("personal property" in label or "contents" in label) \
                    and "contents_value" not in subject_amounts:
                subject_amounts["contents_value"] = (f"{subjects_key}[{s_i}].{amount_key}", amount)

        idx = 0
        main_key, main = _pick(data, "premises_building_occupancy")
        if isinstance(main, dict):
            record = _location_from(main, idx, doc_id, ev, main_key)
            loc_id = record.location_id
            for field, (src_path, amount) in subject_amounts.items():
                parsed = parse_number(amount)
                record.field_evidence[field] = ev.make(
                    src_path, f"locations[{loc_id}].{field}", amount, normalized_value=parsed)
                if field == "building_value":
                    record.building_value = parsed
                else:
                    record.contents_value = parsed
            if record.tiv is None and (record.building_value is not None
                                       or record.contents_value is not None):
                record.tiv = (record.building_value or 0.0) + (record.contents_value or 0.0)
            out.locations.append(record)
            idx += 1
        addl_key, additional = _pick(data, "additional_premises_building_summary_tie_to_sov",
                                     "additional_premises", "additional_locations",
                                     "property_schedule_statement_of_values")
        for j, entry in enumerate(as_list(additional)):
            if not isinstance(entry, dict):
                continue
            # city_state combined fields ("City / ST") are split for matching.
            if "city_state" in entry and "state" not in entry:
                city, _, state = str(entry["city_state"]).partition("/")
                entry = {**entry,
                         "city": entry.get("city") or city.strip() or None,
                         "state": entry.get("state") or state.strip() or None}
            out.locations.append(_location_from(entry, idx, doc_id, ev, f"{addl_key}[{j}]"))
            idx += 1
        for i, entry in enumerate(as_list(get_any(data, "premises", "locations", "buildings"))):
            if isinstance(entry, dict):
                out.locations.append(_location_from(entry, idx + i, doc_id, ev, f"premises[{i}]"))
