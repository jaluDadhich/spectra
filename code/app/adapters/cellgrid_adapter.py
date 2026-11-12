"""Adapter for cell-level spreadsheet extraction JSON (``sheets`` /
``all_nonempty_rows`` shape produced by Excel extractors).

Tables are detected and classified against generic header concepts
(driver roster, fleet schedule, loss detail, SOV, WC payroll, experience
mod history, checklist, external records, key/value measure sheets).
Matched tables become typed canonical records with real
worksheet/row/cell citations; unmatched sheets are preserved as chunked
text evidence so nothing is hidden from downstream review.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.adapters.base import (
    AdaptedDocument,
    DocumentAdapter,
    EvidenceFactory,
)
from app.extraction.cellgrid import CellValue, GridTable, parse_tables
from app.extraction.concepts import (
    MEASURE_ALIASES,
    Concept,
    classify_table,
    normalize_header,
    resolve_document_type,
)
from app.normalization import (
    normalize_date,
    normalize_money,
    normalize_state,
    parse_number,
)
from app.schemas.canonical import (
    ChecklistItem,
    ClaimRecord,
    DriverRecord,
    ExposureRecord,
    ExternalObservation,
    LocationRecord,
    QuoteTermRecord,
    RatingFactorRecord,
    SourceLocation,
    VehicleRecord,
    WCPayrollRow,
)
from app.schemas.envelope import InputEnvelope

_VALUATION_RE = re.compile(r"valuation date[:\s]+([0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE)


def _convert(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().startswith("="):
        return None  # spreadsheet formula text is not a value
    if kind == "money":
        amount, _ = normalize_money(value)
        return amount
    if kind == "number":
        return parse_number(value)
    if kind == "int":
        num = parse_number(value)
        return int(num) if num is not None else None
    if kind == "date":
        return normalize_date(value)
    if kind == "state":
        return normalize_state(value)
    return str(value) if not isinstance(value, str) else value.strip()


class CellGridWorkbookAdapter(DocumentAdapter):
    document_type = "excel_workbook"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        tables = parse_tables(data)
        matched: List[Tuple[GridTable, Concept, Dict[str, str]]] = []
        unmatched: List[GridTable] = []
        for table in tables:
            classified = classify_table(table)
            if classified is None:
                unmatched.append(table)
            else:
                matched.append((table, classified[0], classified[1]))

        concepts = [concept for _, concept, _ in matched]
        resolved = resolve_document_type(concepts)
        if resolved:
            out.document.document_type = resolved
            out.document.recognized = True

        for table, concept, mapping in matched:
            handler = getattr(self, f"_emit_{concept.name}", None)
            if handler is not None:
                handler(table, concept, mapping, doc_id, ev, out)
            self._emit_table_text(table, doc_id, ev)
        for table in unmatched:
            self._emit_table_text(table, doc_id, ev)

    # ------------------------------------------------------------------
    def _loc(self, cell: CellValue, table: GridTable, json_path: str) -> SourceLocation:
        return SourceLocation(
            sheet=table.sheet_name, row=cell.row, column=cell.column,
            cell=cell.coordinate, json_path=json_path, available=True)

    def _cell_ev(self, ev: EvidenceFactory, table: GridTable, cell: CellValue,
                 canonical_field: str, raw: Any, normalized: Any = None,
                 unit: Optional[str] = None, period: Optional[str] = None) -> str:
        json_path = table.cell_json_path(cell.row_index, cell.cell_index)
        snippet = (f"Formula {cell.formula}; cached result {raw}"
                   if cell.formula and raw is not None else None)
        return ev.make(json_path, canonical_field, raw,
                       normalized_value=normalized, unit=unit, period=period,
                       snippet=snippet,
                       source_location_override=self._loc(cell, table, json_path))

    def _row_values(self, table: GridTable, concept: Concept, mapping: Dict[str, str],
                    row: Dict[str, CellValue]) -> Dict[str, Tuple[CellValue, Any, Any]]:
        """Return field -> (cell, raw, converted) for mapped fields present."""
        out: Dict[str, Tuple[CellValue, Any, Any]] = {}
        fields_by_name = {f.name: f for f in concept.fields}
        for fname, header in mapping.items():
            cell = row.get(header)
            if cell is None:
                continue
            kind = fields_by_name[fname].kind
            raw_value = (cell.cached_value
                         if cell.formula and cell.cached_value is not None
                         else cell.value)
            converted = _convert(raw_value, kind)
            if converted is None or converted == "":
                continue
            out[fname] = (cell, raw_value, converted)
        return out

    # ------------------------------------------------------------------
    def _emit_fleet_schedule(self, table, concept, mapping, doc_id, ev, out):
        for i, (row_number, _, row) in enumerate(table.data_rows):
            values = self._row_values(table, concept, mapping, row)
            unit = values.get("unit_id")
            rid = str(unit[2]) if unit else f"{doc_id}#veh{i}"
            fe: Dict[str, str] = {}
            for fname, (cell, raw, conv) in values.items():
                fe[fname] = self._cell_ev(ev, table, cell, f"vehicles[{rid}].{fname}", raw, conv)
            out.vehicles.append(VehicleRecord(
                record_id=rid, document_id=doc_id, field_evidence=fe,
                unit_id=rid,
                vin=(str(values["vin"][2]) if "vin" in values else None),
                status=(str(values["status"][2]).lower() if "status" in values else None),
                gvwr=(values["gvwr"][2] if "gvwr" in values else None),
                vehicle_type=(str(values["vehicle_type"][2]) if "vehicle_type" in values else None),
                garaging_state=(values["garaging_state"][2] if "garaging_state" in values else None),
                garaging_address=(str(values["garaging_address"][2]) if "garaging_address" in values else None),
                assigned_driver_id=(str(values["assigned_driver_id"][2]) if "assigned_driver_id" in values else None),
                cost_new=(values["cost_new"][2] if "cost_new" in values else None)))

    def _emit_driver_roster(self, table, concept, mapping, doc_id, ev, out):
        for i, (row_number, _, row) in enumerate(table.data_rows):
            values = self._row_values(table, concept, mapping, row)
            key = values.get("driver_id")
            rid = str(key[2]) if key else f"{doc_id}#drv{i}"
            fe: Dict[str, str] = {}
            for fname, (cell, raw, conv) in values.items():
                fe[fname] = self._cell_ev(ev, table, cell, f"drivers[{rid}].{fname}", raw, conv)
            endorsements: List[str] = []
            if "cdl_endorsements" in values:
                endorsements = [e.strip() for e in re.split(r"[,;/]", str(values["cdl_endorsements"][2]))
                                if e.strip()]
            out.drivers.append(DriverRecord(
                record_id=rid, document_id=doc_id, field_evidence=fe,
                driver_id=rid,
                name=(str(values["name"][2]) if "name" in values else None),
                license_class=(str(values["license_class"][2]) if "license_class" in values else None),
                cdl_endorsements=endorsements,
                mvr_review_date=(values["mvr_review_date"][2] if "mvr_review_date" in values else None),
                hire_date=(values["hire_date"][2] if "hire_date" in values else None)))

    def _emit_loss_detail(self, table, concept, mapping, doc_id, ev, out):
        m = _VALUATION_RE.search(table.title_text or "")
        valuation = m.group(1) if m else None
        for i, (row_number, _, row) in enumerate(table.data_rows):
            values = self._row_values(table, concept, mapping, row)
            key = values.get("claim_id")
            rid = str(key[2]) if key else f"{doc_id}#claim{i}"
            paid = values["paid"][2] if "paid" in values else None
            if paid is None:
                parts = [values[k][2] for k in ("paid_indemnity", "paid_expense") if k in values]
                paid = sum(parts) if parts else None
            reserves = values["reserves"][2] if "reserves" in values else None
            if reserves is None:
                parts = [values[k][2] for k in ("reserve_indemnity", "reserve_expense") if k in values]
                reserves = sum(parts) if parts else None
            fe: Dict[str, str] = {}
            for fname, (cell, raw, conv) in values.items():
                unit = "USD" if fname in {"paid", "paid_indemnity", "paid_expense",
                                          "reserve_indemnity", "reserve_expense",
                                          "reserves", "incurred", "calculated_incurred",
                                          "incurred_variance"} else None
                fe[fname] = self._cell_ev(ev, table, cell, f"claims[{rid}].{fname}", raw, conv,
                                          unit=unit, period=valuation)
            out.claims.append(ClaimRecord(
                record_id=rid, document_id=doc_id, field_evidence=fe, claim_id=rid,
                line_of_business=(str(values["line_of_business"][2]) if "line_of_business" in values else None),
                loss_date=(values["loss_date"][2] if "loss_date" in values else None),
                paid=paid,
                reserves=reserves,
                incurred_reported=(values["incurred"][2] if "incurred" in values else None),
                status=(str(values["status"][2]) if "status" in values else None),
                valuation_date=valuation,
                policy_year=(str(values["policy_year"][2]) if "policy_year" in values else None)))

    def _emit_sov(self, table, concept, mapping, doc_id, ev, out):
        for i, (row_number, _, row) in enumerate(table.data_rows):
            values = self._row_values(table, concept, mapping, row)
            key = values.get("location_id")
            rid = str(key[2]) if key else f"{doc_id}#loc{i}"
            fe: Dict[str, str] = {}
            for fname, (cell, raw, conv) in values.items():
                unit = "USD" if fname in {"building_value", "contents_value",
                                          "business_income", "tiv"} else None
                fe[fname] = self._cell_ev(ev, table, cell, f"locations[{rid}].{fname}", raw, conv,
                                          unit=unit)
            bldg = values["building_value"][2] if "building_value" in values else None
            contents = values["contents_value"][2] if "contents_value" in values else None
            tiv = values["tiv"][2] if "tiv" in values else None
            if tiv is None and (bldg is not None or contents is not None):
                tiv = (bldg or 0.0) + (contents or 0.0)
            out.locations.append(LocationRecord(
                record_id=rid, document_id=doc_id, field_evidence=fe,
                location_id=rid,
                address=(str(values["address"][2]) if "address" in values else None),
                city=(str(values["city"][2]) if "city" in values else None),
                state=(values["state"][2] if "state" in values else None),
                postal_code=(str(values["zip"][2]) if "zip" in values else None),
                building_value=bldg, contents_value=contents, tiv=tiv,
                square_footage=(values["square_footage"][2] if "square_footage" in values else None)))

    def _emit_wc_payroll(self, table, concept, mapping, doc_id, ev, out):
        for i, (row_number, _, row) in enumerate(table.data_rows):
            values = self._row_values(table, concept, mapping, row)
            rid = f"{doc_id}#wc{i}"
            fe: Dict[str, str] = {}
            for fname, (cell, raw, conv) in values.items():
                unit = "USD" if fname in {"payroll", "manual_premium"} else None
                fe[fname] = self._cell_ev(ev, table, cell, f"wc_rows[{rid}].{fname}", raw, conv,
                                          unit=unit)
            payroll = values["payroll"][2] if "payroll" in values else None
            rate = values["rate"][2] if "rate" in values else None
            class_code = (str(values["class_code"][2]) if "class_code" in values else None)
            state = (values["state"][2] if "state" in values else None)
            out.wc_payroll_rows.append(WCPayrollRow(
                record_id=rid, document_id=doc_id, field_evidence=fe,
                class_code=class_code, state=state,
                location_id=(str(values["location_id"][2]) if "location_id" in values else None),
                payroll=payroll, rate=rate,
                full_time_count=(int(values["full_time"][2]) if "full_time" in values else None),
                part_time_count=(int(values["part_time"][2]) if "part_time" in values else None)))
            if rate is not None and class_code and state:
                fe_rate = {"value": fe["rate"]} if "rate" in fe else {}
                out.rating_factors.append(RatingFactorRecord(
                    record_id=f"{rid}#rate", document_id=doc_id, field_evidence=fe_rate,
                    factor_type="class_rate", value=rate, state=state,
                    class_code=class_code, source_authority="workbook"))

    def _emit_emod_history(self, table, concept, mapping, doc_id, ev, out):
        for i, (row_number, _, row) in enumerate(table.data_rows):
            values = self._row_values(table, concept, mapping, row)
            factor = values["factor"][2] if "factor" in values else None
            if factor is None:
                continue
            eff = values["effective_date"][2] if "effective_date" in values else None
            currency = str(values["currency_of_term"][2]).lower() if "currency_of_term" in values else ""
            is_current = "current" in currency
            fe: Dict[str, str] = {}
            for fname, (cell, raw, conv) in values.items():
                fe[fname] = self._cell_ev(
                    ev, table, cell, f"rating_factors[emod_history[{i}]].{fname}", raw, conv,
                    period=eff)
            out.rating_factors.append(RatingFactorRecord(
                record_id=f"{doc_id}#emodhist{i}", document_id=doc_id, field_evidence=fe,
                factor_type="experience_mod" if is_current else "experience_mod_history",
                value=factor, effective_date=eff,
                source_authority=(str(values["source"][2]) if "source" in values else "workbook")))

    def _emit_checklist(self, table, concept, mapping, doc_id, ev, out):
        for i, (row_number, _, row) in enumerate(table.data_rows):
            values = self._row_values(table, concept, mapping, row)
            key = values.get("item")
            name = str(key[2]) if key else f"item{i}"
            fe: Dict[str, str] = {}
            if key:
                fe["status"] = self._cell_ev(
                    ev, table, values["status"][0], f"checklist[{name}].status",
                    values["status"][1], values["status"][2]) if "status" in values else ""
            fe = {k: v for k, v in fe.items() if v}
            out.checklist_items.append(ChecklistItem(
                record_id=f"{doc_id}#{name}", document_id=doc_id, field_evidence=fe,
                item=name,
                status=(str(values["status"][2]) if "status" in values else None),
                note=(str(values["note"][2]) if "note" in values else None)))

    def _emit_external_records(self, table, concept, mapping, doc_id, ev, out):
        for i, (row_number, _, row) in enumerate(table.data_rows):
            values = self._row_values(table, concept, mapping, row)
            rid = f"{doc_id}#ext{table.sheet_index}_{i}"
            first_cell = next(iter(row.values()), None)
            if first_cell is None:
                continue
            eid = self._cell_ev(ev, table, first_cell, f"external[{rid}].record",
                                {h: c.value for h, c in row.items()})
            fields: Dict[str, Any] = {}
            for fname, (cell, raw, conv) in values.items():
                if fname in {"entity_name", "address", "url", "status"}:
                    fields[fname] = conv
            if "summary" in values:
                fields["services"] = [str(values["summary"][2])]
            subject = str(values["record_type"][2]) if "record_type" in values else None
            out.external_observations.append(ExternalObservation(
                record_id=rid, document_id=doc_id, field_evidence={"record": eid},
                source_name=table.sheet_name,
                outcome_state="POSSIBLE_MATCH",
                subject=subject,
                record_url=(str(values["url"][2]) if "url" in values else None),
                retrieved_at=(values["retrieved"][2] if "retrieved" in values else None),
                match_rationale=None,
                limitations=["Supplied external record; identity not yet resolved"],
                fields=fields))

    def _emit_kv_measures(self, table, concept, mapping, doc_id, ev, out):
        measure_header = mapping.get("measure")
        value_header = mapping.get("value")
        if not measure_header or not value_header:
            return
        loss_incurred: Optional[Tuple[CellValue, float]] = None
        loss_count: Optional[Tuple[CellValue, float]] = None
        for row_number, _, row in table.data_rows:
            measure_cell = row.get(measure_header)
            value_cell = row.get(value_header)
            if measure_cell is None or value_cell is None:
                continue
            measure = normalize_header(str(measure_cell.value))
            # Tolerate unit suffixes: header normalization drops parens but
            # keeps "$"/units, e.g. "carrier summary reported incurred $".
            trimmed = re.sub(r"[\s$]+$", "", re.sub(r"\s*\([^)]*\)\s*$", "", measure)).strip()
            target = MEASURE_ALIASES.get(measure) or MEASURE_ALIASES.get(trimmed)
            if target is None:
                continue
            exposure_type, unit = target
            number = parse_number(value_cell.value)
            if number is None:
                continue
            if exposure_type == "loss_summary_incurred":
                loss_incurred = (value_cell, number)
                continue
            if exposure_type == "loss_summary_count":
                loss_count = (value_cell, number)
                continue
            eid = self._cell_ev(ev, table, value_cell, f"exposures[{exposure_type}].value",
                                value_cell.value, number, unit=unit)
            out.exposures.append(ExposureRecord(
                record_id=f"{doc_id}#{exposure_type}", document_id=doc_id,
                field_evidence={"value": eid}, exposure_type=exposure_type,
                value=number, unit=unit, description=str(measure_cell.value)))
        if loss_incurred is not None or loss_count is not None:
            fe: Dict[str, str] = {}
            if loss_incurred is not None:
                cell, number = loss_incurred
                fe["total_incurred"] = self._cell_ev(
                    ev, table, cell, "loss_summary.total_incurred", cell.value, number, unit="USD")
            if loss_count is not None:
                cell, number = loss_count
                fe["claim_count"] = self._cell_ev(
                    ev, table, cell, "loss_summary.claim_count", cell.value, number, unit="count")
            out.exposures.append(ExposureRecord(
                record_id=f"{doc_id}#summary", document_id=doc_id, field_evidence=fe,
                exposure_type="loss_summary",
                value=loss_incurred[1] if loss_incurred else None, unit="USD",
                description=(f"declared_count={int(loss_count[1])}"
                             if loss_count else "declared_count=unknown")))

    # ------------------------------------------------------------------
    def _emit_table_text(self, table: GridTable, doc_id: str, ev: EvidenceFactory) -> None:
        """Preserve the full table (all columns) as chunked text evidence so
        LLM review and extraction see unmapped content too."""
        if table.title_text:
            path = f"{table.path_prefix}[0:{table.header_row_index}]"
            ev.make(path, f"worksheet[{table.sheet_name}].context", table.title_text,
                    snippet=table.title_text[:160],
                    source_location_override=SourceLocation(
                        sheet=table.sheet_name, row=1, json_path=path, available=True))
        rows = table.data_rows
        for start in range(0, len(rows), 20):
            chunk = rows[start:start + 20]
            first = chunk[0][0]
            lines = [" | ".join(table.headers)]
            for _, _, by_header in chunk:
                lines.append(" | ".join(
                    str(by_header[h].value).replace("\n", " ").strip() if h in by_header else ""
                    for h in table.headers))
            text = "\n".join(lines)
            path = f"{table.path_prefix}[rows {first}:{chunk[-1][0]}]"
            ev.make(path, f"worksheet[{table.sheet_name}].table_rows", text,
                    snippet=text[:160],
                    source_location_override=SourceLocation(
                        sheet=table.sheet_name, row=first, json_path=path, available=True))
