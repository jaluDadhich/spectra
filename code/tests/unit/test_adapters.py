import json

from app.adapters.loader import load_json_file, parse_envelope
from app.adapters.combined import _section_source
from app.catalog import get_registry
from tests.conftest import envelope, make_case


def test_envelope_parsing_preferred_shape():
    doc = parse_envelope(envelope("loss_run", {"claims": []}), "x.json")
    assert doc.envelope.document.document_type == "loss_run"
    assert not doc.validation_errors


def test_bare_json_wrapped_without_fabricated_provenance():
    doc = parse_envelope({"claims": [{"claim_id": "C1"}]}, "bare.json")
    assert doc.envelope.document.document_type is None
    assert doc.envelope.provenance.extraction_source == "bare_json"
    case = make_case([{"claims": [{"claim_id": "C1", "paid": 5}]}])
    claim = case.claims[0]
    assert claim.claim_id == "C1"
    ev = case.evidence_store[claim.field_evidence["paid"]]
    assert ev.source_location.available is False  # never fabricate citations
    assert ev.raw_value == 5


def test_invalid_json_reported(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    data, errors = load_json_file(bad)
    assert data is None and errors
    arr = tmp_path / "arr.json"
    arr.write_text(json.dumps([1, 2]), encoding="utf-8")
    data, errors = load_json_file(arr)
    assert data is None and "object" in errors[0]


def test_unknown_type_preserved_as_unsupported():
    case = make_case([envelope("telematics_feed", {"some_field": 1})])
    doc = case.documents[0]
    assert doc.document_type == "unsupported"
    assert doc.recognized is False
    assert case.evidence_store  # shallow evidence preserved


def test_generated_document_ids_marked():
    env = envelope("quote", {"terms": []})
    env["document"]["document_id"] = None
    case = make_case([env])
    assert case.documents[0].generated_id is True
    assert case.documents[0].document_id.startswith("GEN-")


def test_content_sniffing_without_declared_type():
    registry = get_registry()
    assert registry.sniff({"payroll_rows": []}) == "wc_rating_workbook"
    assert registry.sniff({"claims": []}) == "loss_run"
    assert registry.sniff({"vehicles": []}) == "fleet_schedule"
    assert registry.sniff({"xyz": 1}) is None


def test_provenance_attached_when_supplied():
    env = envelope("wc_rating_workbook", {"payroll_rows": [
        {"class_code": "8810", "state": "CA", "payroll": 100}]},
        fields={"payroll_rows[0].payroll": {
            "source_sheet": "Payroll", "source_row": 5, "source_cell": "D5",
            "extraction_confidence": 0.98}})
    case = make_case([env])
    row = case.wc_payroll_rows[0]
    ev = case.evidence_store[row.field_evidence["payroll"]]
    assert ev.source_location.available is True
    assert ev.source_location.sheet == "Payroll"
    assert ev.source_location.cell == "D5"
    assert ev.extraction_confidence == 0.98


def test_cell_grid_workbook_creates_typed_records_and_cell_citations():
    data = {"sheets": [{
        "sheet_name": "Fleet Schedule",
        "all_nonempty_rows": [
            {"row": 1, "cells": [{"coordinate": "A1", "value": "Vehicle schedule"}]},
            {"row": 3, "cells": [
                {"coordinate": "A3", "value": "Unit Number"},
                {"coordinate": "B3", "value": "VIN"},
                {"coordinate": "C3", "value": "Vehicle Type"},
                {"coordinate": "D3", "value": "GVWR"},
                {"coordinate": "E3", "value": "Status"},
            ]},
            {"row": 4, "cells": [
                {"coordinate": "A4", "value": "TRK-1"},
                {"coordinate": "B4", "value": "1FTYR10U33PA12345"},
                {"coordinate": "C4", "value": "Tractor"},
                {"coordinate": "D4", "value": 33000},
                {"coordinate": "E4", "value": "Active"},
            ]},
        ],
    }]}
    case = make_case([envelope("excel_workbook", data)])

    assert len(case.vehicles) == 1
    assert case.vehicles[0].unit_id == "TRK-1"
    assert case.vehicles[0].gvwr == 33000
    evidence = case.evidence_store[case.vehicles[0].field_evidence["gvwr"]]
    assert evidence.source_location.available is True
    assert evidence.source_location.sheet == "Fleet Schedule"
    assert evidence.source_location.row == 4
    assert evidence.source_location.cell == "D4"


def test_combined_acord_splits_versions_and_keeps_scope_citations():
    combined = envelope("acord_combined_125_126_130_140", {
        "acord_125": {"applicant": {"legal_name": "Example Holdings"}},
        "acord_126": {"claims_history": [{
            "year": "2025", "claim_count_all_lines": 2,
        }]},
        "acord_130": {"class_rows": []},
        "acord_140": {
            "received": {"premises": [{
                "location_id": "L1", "address": "1 Main St", "state": "CA",
                "building_value": 100000,
            }]},
            "resubmitted": {"premises": [{
                "location_id": "L1", "address": "1 Main St", "state": "CA",
                "building_value": 125000,
            }]},
        },
    }, doc_id="COMBINED", fields={
        "data.acord_140.received.premises[0].building_value": {
            "source_page": 2,
        },
    })
    case = make_case([combined])

    versions = [doc for doc in case.documents if doc.document_type == "acord_140"]
    assert len(versions) == 2
    received_evidence = next(
        item for item in case.evidence_store.values()
        if item.raw_value == 100000
        and item.document_id.endswith("#acord_140#received"))
    assert received_evidence.source_location.available is True
    assert received_evidence.source_location.page == 2
    history = next(exp for exp in case.exposures
                   if exp.exposure_type == "annual_claim_count")
    assert history.description == "all lines"


def test_combined_acord_source_map_is_selected_per_version():
    ranges = {
        "acord_140_received": {"source_file": "property-received.pdf", "pages": [1, 3]},
        "acord_140_resubmitted": {"source_file": "property-resubmitted.pdf", "pages": [1, 3]},
    }
    assert _section_source(ranges, [], "acord_140", "received")["source_file"] == \
        "property-received.pdf"
    assert _section_source(ranges, [], "acord_140", "resubmitted")["source_file"] == \
        "property-resubmitted.pdf"
    assert _section_source(ranges, [], "acord_140", None) is None
