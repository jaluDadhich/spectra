"""Adapters for WC rating workbooks, draft quotes, text/narrative
documents, submission checklists, and external research records."""
from __future__ import annotations

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
    normalize_money,
    normalize_state,
    parse_number,
)
from app.schemas.canonical import (
    ChecklistItem,
    ExposureRecord,
    ExternalObservation,
    NarrativeRecord,
    QuoteTermRecord,
    RatingFactorRecord,
    SourceLocation,
    WCPayrollRow,
)
from app.schemas.envelope import InputEnvelope


class WcRatingWorkbookAdapter(DocumentAdapter):
    document_type = "wc_rating_workbook"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        for i, row in enumerate(as_list(get_any(data, "payroll_rows", "payroll_by_class", "rows"))):
            if not isinstance(row, dict):
                continue
            rid = str(get_any(row, "row_id", "id") or f"{doc_id}#row{i}")
            payroll, currency = normalize_money(get_any(row, "payroll", "estimated_payroll"))
            rate = parse_number(get_any(row, "rate", "class_rate"))
            fe = {}
            for field, raw, norm in (
                ("class_code", get_any(row, "class_code", "class"), normalize_class_code(get_any(row, "class_code", "class"))),
                ("state", get_any(row, "state", "state_code"), normalize_state(get_any(row, "state", "state_code"))),
                ("payroll", get_any(row, "payroll", "estimated_payroll"), payroll),
                ("rate", get_any(row, "rate", "class_rate"), rate),
                ("full_time", get_any(row, "full_time", "ft_employees"), parse_number(get_any(row, "full_time", "ft_employees"))),
                ("part_time", get_any(row, "part_time", "pt_employees"), parse_number(get_any(row, "part_time", "pt_employees"))),
            ):
                if raw is not None:
                    fe[field] = ev.make(f"payroll_rows[{i}].{field}", f"wc_rows[{rid}].{field}", raw,
                                        normalized_value=norm,
                                        unit=currency if field == "payroll" else None)
            out.wc_payroll_rows.append(WCPayrollRow(
                record_id=rid, document_id=doc_id, field_evidence=fe,
                class_code=normalize_class_code(get_any(row, "class_code", "class")),
                state=normalize_state(get_any(row, "state", "state_code")),
                location_id=(str(l) if (l := get_any(row, "location_id")) is not None else None),
                payroll=payroll, rate=rate,
                effective_date=normalize_date(get_any(row, "effective_date")),
                full_time_count=(int(v) if (v := parse_number(get_any(row, "full_time", "ft_employees"))) is not None else None),
                part_time_count=(int(v) if (v := parse_number(get_any(row, "part_time", "pt_employees"))) is not None else None)))
        for i, entry in enumerate(as_list(get_any(data, "experience_mod_history", "mod_history"))):
            if not isinstance(entry, dict):
                continue
            factor = parse_number(get_any(entry, "factor", "value", "mod"))
            eff = normalize_date(get_any(entry, "effective_date"))
            fe = {}
            if factor is not None:
                fe["value"] = ev.make(f"experience_mod_history[{i}].factor",
                                      f"rating_factors[experience_mod_history[{i}]].value",
                                      get_any(entry, "factor", "value", "mod"),
                                      normalized_value=factor, period=eff)
            out.rating_factors.append(RatingFactorRecord(
                record_id=f"{doc_id}#emodhist{i}", document_id=doc_id, field_evidence=fe,
                factor_type="experience_mod_history", value=factor, effective_date=eff,
                source_authority=get_any(entry, "source", default="workbook")))
        current = get_any(data, "current_experience_mod", "current_mod", default=None)
        if isinstance(current, dict):
            factor = parse_number(get_any(current, "factor", "value", "mod"))
            eff = normalize_date(get_any(current, "effective_date"))
            fe = {}
            if factor is not None:
                fe["value"] = ev.make("current_experience_mod.factor",
                                      "rating_factors[current_experience_mod].value",
                                      get_any(current, "factor", "value", "mod"),
                                      normalized_value=factor, period=eff)
            out.rating_factors.append(RatingFactorRecord(
                record_id=f"{doc_id}#emodcurrent", document_id=doc_id, field_evidence=fe,
                factor_type="experience_mod", value=factor, effective_date=eff,
                source_authority=get_any(current, "source", default="workbook")))
        for i, entry in enumerate(as_list(get_any(data, "rate_tables", "rates"))):
            if not isinstance(entry, dict):
                continue
            rate = parse_number(get_any(entry, "rate", "class_rate"))
            fe = {}
            if rate is not None:
                fe["value"] = ev.make(f"rate_tables[{i}].rate", f"rating_factors[rate_tables[{i}]].rate",
                                      get_any(entry, "rate", "class_rate"), normalized_value=rate)
            out.rating_factors.append(RatingFactorRecord(
                record_id=f"{doc_id}#rate{i}", document_id=doc_id, field_evidence=fe,
                factor_type="class_rate", value=rate,
                effective_date=normalize_date(get_any(entry, "effective_date")),
                state=normalize_state(get_any(entry, "state")),
                class_code=normalize_class_code(get_any(entry, "class_code", "class")),
                source_authority=get_any(entry, "source", default="workbook")))
        totals = get_any(data, "declared_totals", "totals", default=None)
        if isinstance(totals, dict):
            total, _ = normalize_money(get_any(totals, "total_payroll", "payroll"))
            if total is not None:
                eid = ev.make("declared_totals.total_payroll", "wc_workbook.total_payroll",
                              get_any(totals, "total_payroll", "payroll"),
                              normalized_value=total, unit="USD")
                out.exposures.append(ExposureRecord(
                    record_id=f"{doc_id}#total_payroll", document_id=doc_id,
                    field_evidence={"value": eid}, exposure_type="declared_payroll_total",
                    value=total, unit="USD"))


class QuoteAdapter(DocumentAdapter):
    document_type = "quote"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        for i, term in enumerate(as_list(get_any(data, "terms", "quote_terms"))):
            if not isinstance(term, dict):
                continue
            rid = str(get_any(term, "term_id", "name") or f"{doc_id}#term{i}")
            value = get_any(term, "value")
            num, currency = normalize_money(value)
            eid = ev.make(f"terms[{i}].value", f"quote_terms[{rid}].value", value,
                          normalized_value=num if num is not None else value,
                          unit=currency or get_any(term, "unit"))
            out.quote_terms.append(QuoteTermRecord(
                record_id=rid, document_id=doc_id, field_evidence={"value": eid},
                term_type=str(get_any(term, "term_type", "type", default="limit")),
                coverage=get_any(term, "coverage"), name=get_any(term, "name"),
                value=num if num is not None else value,
                unit=currency or get_any(term, "unit"), scope=get_any(term, "scope")))
        rating = get_any(data, "rating_inputs", default={}) or {}
        if isinstance(rating, dict):
            self._rating_inputs(rating, doc_id, ev, out)
        statements = get_any(data, "statements", default={}) or {}
        if isinstance(statements, dict):
            for key, value in statements.items():
                eid = ev.make(f"statements.{key}", f"quote_statements.{key}", value)
                out.checklist_items.append(ChecklistItem(
                    record_id=f"{doc_id}#stmt_{key}", document_id=doc_id,
                    field_evidence={"status": eid}, item=f"quote_statement:{key}",
                    status=str(value), note=None))
        for i, op in enumerate(as_list(get_any(data, "operations_described", "operations"))):
            eid = ev.make(f"operations_described[{i}]", f"quote_operations[{i}]", op)
            out.exposures.append(ExposureRecord(
                record_id=f"{doc_id}#qop{i}", document_id=doc_id,
                field_evidence={"description": eid}, exposure_type="quote_operation",
                value=None, description=str(op)))

    def _rating_inputs(self, rating: dict, doc_id: str, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        for i, row in enumerate(as_list(get_any(rating, "payroll_by_class", "payroll"))):
            if not isinstance(row, dict):
                continue
            rid = f"{doc_id}#qpay{i}"
            payroll, currency = normalize_money(get_any(row, "payroll", "amount"))
            fe = {}
            for field, raw, norm in (
                ("class_code", get_any(row, "class_code", "class"), normalize_class_code(get_any(row, "class_code", "class"))),
                ("state", get_any(row, "state"), normalize_state(get_any(row, "state"))),
                ("payroll", get_any(row, "payroll", "amount"), payroll),
            ):
                if raw is not None:
                    fe[field] = ev.make(f"rating_inputs.payroll_by_class[{i}].{field}",
                                        f"quote_payroll[{rid}].{field}", raw, normalized_value=norm,
                                        unit=currency if field == "payroll" else None)
            out.wc_payroll_rows.append(WCPayrollRow(
                record_id=rid, document_id=doc_id, field_evidence=fe,
                class_code=normalize_class_code(get_any(row, "class_code", "class")),
                state=normalize_state(get_any(row, "state")), payroll=payroll))
        emod = get_any(rating, "experience_mod", default=None)
        if isinstance(emod, dict):
            factor = parse_number(get_any(emod, "factor", "value"))
            eff = normalize_date(get_any(emod, "effective_date"))
            fe = {}
            if factor is not None:
                fe["value"] = ev.make("rating_inputs.experience_mod.factor",
                                      "rating_factors[quote_experience_mod].value",
                                      get_any(emod, "factor", "value"),
                                      normalized_value=factor, period=eff)
            out.rating_factors.append(RatingFactorRecord(
                record_id=f"{doc_id}#qemod", document_id=doc_id, field_evidence=fe,
                factor_type="experience_mod", value=factor, effective_date=eff,
                source_authority="quote"))
        for i, row in enumerate(as_list(get_any(rating, "class_rates", "rates"))):
            if not isinstance(row, dict):
                continue
            rate = parse_number(get_any(row, "rate", "class_rate"))
            fe = {}
            if rate is not None:
                fe["value"] = ev.make(f"rating_inputs.class_rates[{i}].rate",
                                      f"rating_factors[quote_rate[{i}]].rate",
                                      get_any(row, "rate", "class_rate"), normalized_value=rate)
            out.rating_factors.append(RatingFactorRecord(
                record_id=f"{doc_id}#qrate{i}", document_id=doc_id, field_evidence=fe,
                factor_type="class_rate", value=rate,
                effective_date=normalize_date(get_any(row, "effective_date")),
                state=normalize_state(get_any(row, "state")),
                class_code=normalize_class_code(get_any(row, "class_code", "class")),
                source_authority="quote"))
        vcount = parse_number(get_any(rating, "vehicle_count", "power_units"))
        if vcount is not None:
            eid = ev.make("rating_inputs.vehicle_count", "quote_rating.vehicle_count",
                          get_any(rating, "vehicle_count", "power_units"),
                          normalized_value=vcount, unit="count")
            out.exposures.append(ExposureRecord(
                record_id=f"{doc_id}#qveh", document_id=doc_id, field_evidence={"value": eid},
                exposure_type="vehicle_count", value=vcount, unit="count"))
        sales, currency = normalize_money(get_any(rating, "gross_sales", "sales"))
        if sales is not None:
            eid = ev.make("rating_inputs.gross_sales", "quote_rating.gross_sales",
                          get_any(rating, "gross_sales", "sales"),
                          normalized_value=sales, unit=currency or "USD")
            out.exposures.append(ExposureRecord(
                record_id=f"{doc_id}#qsales", document_id=doc_id, field_evidence={"value": eid},
                exposure_type="sales", value=sales, unit=currency or "USD"))


class BrokerEmailAdapter(DocumentAdapter):
    document_type = "broker_email"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        body = get_any(data, "body", "text", "narrative", default="")
        subject = get_any(data, "subject")
        eid = ev.make("body", f"narratives[{doc_id}].text", body)
        out.narratives.append(NarrativeRecord(
            record_id=f"{doc_id}#body", document_id=doc_id,
            field_evidence={"text": eid}, text=str(body),
            subject=str(subject) if subject else "broker_email"))


class ChecklistAdapter(DocumentAdapter):
    document_type = "submission_checklist"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        for i, item in enumerate(as_list(get_any(data, "items", "checklist"))):
            if not isinstance(item, dict):
                continue
            name = str(get_any(item, "item", "document", "name", default=f"item{i}"))
            status = get_any(item, "status", "state")
            eid = ev.make(f"items[{i}].status", f"checklist[{name}].status", status)
            out.checklist_items.append(ChecklistItem(
                record_id=f"{doc_id}#{name}", document_id=doc_id,
                field_evidence={"status": eid}, item=name,
                status=str(status) if status is not None else None,
                note=get_any(item, "note", "comment")))


class OpsSupplementAdapter(DocumentAdapter):
    document_type = "ops_supplement"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        for i, op in enumerate(as_list(get_any(data, "operations", "services"))):
            eid = ev.make(f"operations[{i}]", f"ops_supplement[{i}]", op)
            desc = op.get("description") if isinstance(op, dict) else str(op)
            loc = op.get("location_id") if isinstance(op, dict) else None
            out.exposures.append(ExposureRecord(
                record_id=f"{doc_id}#op{i}", document_id=doc_id,
                field_evidence={"description": eid}, exposure_type="declared_operation",
                location_id=(str(loc) if loc is not None else None), description=str(desc)))
        narrative = get_any(data, "narrative", "notes")
        if narrative:
            eid = ev.make("narrative", f"narratives[{doc_id}].text", narrative)
            out.narratives.append(NarrativeRecord(
                record_id=f"{doc_id}#narr", document_id=doc_id,
                field_evidence={"text": eid}, text=str(narrative), subject="ops_supplement"))
        peak = get_any(data, "peak_shipment_value", "max_cargo_value")
        if peak is not None:
            amount, currency = normalize_money(peak)
            eid = ev.make("peak_shipment_value", "exposures[cargo].value", peak,
                          normalized_value=amount, unit=currency or "USD")
            out.exposures.append(ExposureRecord(
                record_id=f"{doc_id}#cargo", document_id=doc_id,
                field_evidence={"value": eid}, exposure_type="cargo_value",
                value=amount, unit=currency or "USD",
                description=get_any(data, "cargo_description")))


class ExternalRecordsAdapter(DocumentAdapter):
    """Structured external/public-record workbook supplied as JSON.

    These are NOT live lookups; they are records supplied with the
    submission. Identity matching is performed conservatively by the
    entity-resolution/external-check rules, not here.
    """

    document_type = "external_records"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        for i, rec in enumerate(as_list(get_any(data, "observations", "records"))):
            if not isinstance(rec, dict):
                continue
            rid = str(get_any(rec, "record_id", "id") or f"{doc_id}#ext{i}")
            record_url = get_any(rec, "record_url", "url")
            page_value = parse_number(get_any(rec, "source_page", "page"))
            source_location = SourceLocation(
                page=int(page_value) if page_value is not None else None,
                url=str(record_url) if record_url else None,
                json_path=f"observations[{i}]", available=bool(record_url))
            eid = ev.make(f"observations[{i}]", f"external[{rid}]", rec,
                          source_location_override=source_location)
            nested_fields = rec.get("fields")
            observation_fields = (dict(nested_fields)
                                  if isinstance(nested_fields, dict) else {})
            observation_fields.update({
                key: value for key, value in rec.items()
                if key not in {"record_id", "id", "fields"}
            })
            out.external_observations.append(ExternalObservation(
                record_id=rid, document_id=doc_id, field_evidence={"record": eid},
                source_name=str(get_any(rec, "source_name", "source", default="supplied_external")),
                outcome_state="POSSIBLE_MATCH",  # provisional; resolved by external checks
                subject=get_any(rec, "subject", "record_type"),
                record_identifier=get_any(rec, "record_identifier", "registration_number"),
                record_url=record_url,
                retrieved_at=normalize_date(get_any(rec, "retrieved_at")),
                record_date=normalize_date(get_any(rec, "record_date", "inspection_date")),
                match_rationale=None,
                limitations=["Supplied external record; identity not yet resolved"],
                fields=observation_fields))


class GenericAdapter(DocumentAdapter):
    """Fallback for unknown/unsupported document types. Preserves the
    document and creates shallow evidence for top-level values so the
    content remains inspectable and auditable."""

    document_type = "unsupported"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        # Keep every scalar from unfamiliar schemas available to whole-case
        # semantic review; previously nested arrays/objects were reduced to
        # a type marker, making most of an unknown document invisible to GPT.
        def visit(value, path: str) -> None:
            if isinstance(value, dict):
                if not value:
                    ev.make(path, f"unsupported.{path}", "{}")
                for key, child in value.items():
                    visit(child, f"{path}.{key}" if path else str(key))
            elif isinstance(value, list):
                if not value:
                    ev.make(path, f"unsupported.{path}", "[]")
                for index, child in enumerate(value):
                    visit(child, f"{path}[{index}]")
            else:
                ev.make(path, f"unsupported.{path}", value)

        for key, value in envelope.data.items():
            visit(value, str(key))


class UnstructuredDocumentAdapter(GenericAdapter):
    """Preserve OCR/text and unfamiliar extracted JSON for LLM review."""

    document_type = "unstructured_document"

    @staticmethod
    def _column_number(address: str) -> int:
        letters = "".join(char for char in address if char.isalpha()).upper()
        number = 0
        for char in letters:
            number = number * 26 + ord(char) - ord("A") + 1
        return number

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory,
                out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        text_chunks = get_any(data, "text_chunks")
        if isinstance(text_chunks, list):
            for index, chunk in enumerate(text_chunks):
                if not isinstance(chunk, dict):
                    continue
                text = str(get_any(chunk, "text", "content", default="")).strip()
                if not text:
                    continue
                start = get_any(chunk, "line_start")
                end = get_any(chunk, "line_end", default=start)
                path = f"text_chunks[{index}].text"
                ev.make(
                    path, f"document_text.lines[{start}:{end}]", text,
                    snippet=text[:160],
                    source_location_override=SourceLocation(
                        line_start=start, line_end=end, json_path=path,
                        available=True))
            if ev.items:
                return
        pages = get_any(data, "pages")
        if isinstance(pages, list):
            for index, page in enumerate(pages):
                if not isinstance(page, dict):
                    continue
                text = get_any(page, "full_text", "text")
                if not text:
                    blocks = as_list(get_any(page, "text_blocks"))
                    text = "\n".join(
                        str(get_any(block, "text", "content", default=""))
                        for block in blocks if isinstance(block, dict))
                if not text:
                    continue
                page_number = get_any(page, "page_number", default=index + 1)
                path = f"pages[{index}].full_text"
                ev.make(path, f"document_pages[{page_number}].text", str(text),
                        snippet=str(text)[:160], source_location_override=SourceLocation(
                            page=int(page_number) if str(page_number).isdigit() else None,
                            json_path=path, available=True))
            if ev.items:
                return

        worksheets = get_any(data, "worksheets")
        if isinstance(worksheets, list):
            for sheet_index, sheet in enumerate(worksheets):
                if not isinstance(sheet, dict):
                    continue
                sheet_name = str(get_any(sheet, "name", default=f"Sheet {sheet_index + 1}"))
                rows = as_list(get_any(sheet, "rows"))
                parsed_rows = []
                for row_index, row in enumerate(rows):
                    if not isinstance(row, dict):
                        continue
                    cells = get_any(row, "cells", default={}) or {}
                    if not isinstance(cells, dict):
                        continue
                    ordered = sorted(cells.items(), key=lambda item: self._column_number(item[0]))
                    values = [value.get("value") if isinstance(value, dict) else value
                              for _, value in ordered]
                    nonempty = [value for value in values if value not in (None, "")]
                    if nonempty:
                        parsed_rows.append((row_index, row, ordered, values, nonempty))
                if not parsed_rows:
                    continue
                # Extractors usually place titles/notes in rows 1-2 and the
                # table header in the widest row. Choosing by width supports
                # changing spreadsheet schemas without hard-coding columns.
                header_pos = max(range(len(parsed_rows)),
                                 key=lambda pos: len(parsed_rows[pos][4]))
                header_index, header_row, header_cells, header_values, headers_nonempty = parsed_rows[header_pos]
                headers = [str(value).strip() if value not in (None, "") else f"Column {i + 1}"
                           for i, value in enumerate(header_values)]
                intro = parsed_rows[:header_pos]
                if intro:
                    intro_text = "\n".join(
                        " | ".join(str(v).strip() for v in item[4]) for item in intro)
                    intro_path = f"worksheets[{sheet_index}].rows[0:{header_index}]"
                    ev.make(intro_path, f"worksheet[{sheet_name}].context", intro_text,
                            snippet=intro_text, source_location_override=SourceLocation(
                                sheet=sheet_name, row=1, json_path=intro_path, available=True))
                table_rows = parsed_rows[header_pos + 1:]
                for start in range(0, len(table_rows), 20):
                    chunk = table_rows[start:start + 20]
                    first_source_row = get_any(chunk[0][1], "row_number", default=chunk[0][0] + 1)
                    lines = [" | ".join(headers)]
                    for _, row, _, values, _ in chunk:
                        lines.append(" | ".join(
                            "" if value is None else str(value).replace("\n", " ").strip()
                            for value in values))
                    text = "\n".join(lines)
                    path = (f"worksheets[{sheet_index}].rows["
                            f"{first_source_row}:{first_source_row + len(chunk) - 1}]")
                    ev.make(path, f"worksheet[{sheet_name}].table_rows", text,
                            snippet=text[:160], source_location_override=SourceLocation(
                                sheet=sheet_name, row=int(first_source_row),
                                json_path=path, available=True))
            if ev.items:
                return

        # Arbitrary extracted JSON without a recognized text/table layout.
        super().extract(envelope, ev, out)
