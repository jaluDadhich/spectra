from __future__ import annotations

import json

from app.config import Settings
from app.extraction.llm_extractor import (
    extract_document_exposures,
    resolve_pointer,
)
from app.extraction.store import save_exposure_model
from app.llm import CostTracker, MockGateway
from app.schemas.canonical import CanonicalDocument
from tests.conftest import envelope, make_case


def _tracker() -> CostTracker:
    return CostTracker(5, 1.0, 0.001, 0.002, 0.001, 0.002)


def _document() -> CanonicalDocument:
    return CanonicalDocument(
        document_id="DOC-1",
        document_type="unstructured_document",
        document_name="submission.json",
        raw={
            "general_liability": {
                "annual_sales": 125000,
                "currency": "USD",
                "policy_year": 2025,
                "location_id": "LOC-7",
            },
        },
    )


def _responses(verdict="PASS", accepted=None):
    source_path = "/general_liability/annual_sales"
    return [
        {
            "proposals": [{
                "source_path": source_path,
                "exposure_type": "sales",
                "unit_path": "/general_liability/currency",
                "period_path": "/general_liability/policy_year",
                "location_id_path": "/general_liability/location_id",
            }],
            "summary": "One source-backed sales exposure.",
        },
        {
            "verdict": verdict,
            "accepted_paths": accepted if accepted is not None else [source_path],
            "issues": [] if verdict == "PASS" else ["Source context is ambiguous"],
            "summary": "Independent review result.",
        },
    ]


def test_json_pointer_resolution_and_escaping():
    data = {"a/b": {"~key": [1, 2]}}
    assert resolve_pointer(data, "/a~1b/~0key/1") == (True, 2)
    assert resolve_pointer(data, "/missing") == (False, None)
    assert resolve_pointer(data, "a/b") == (False, None)


def test_llm_extraction_uses_verified_source_value_and_citations(tmp_path):
    settings = Settings(_env_file=None, spectra_max_evidence_payload_chars=10000)
    gateway = MockGateway(scripted=_responses())
    outcome = extract_document_exposures(gateway, _tracker(), settings, _document())

    assert outcome.status == "ACCEPTED"
    assert len(outcome.exposures) == len(outcome.evidence) == 1
    exposure = outcome.exposures[0]
    evidence = outcome.evidence[0]
    assert exposure.value == 125000
    assert exposure.unit == "USD"
    assert exposure.period == "2025"
    assert exposure.location_id == "LOC-7"
    assert evidence.raw_value == 125000
    assert evidence.source_location.available is True
    assert evidence.source_location.json_path == "/general_liability/annual_sales"
    assert outcome.proposed_count == 1 and outcome.rejected_count == 0
    assert "125000" not in json.dumps(gateway.calls)
    assert "LOC-7" not in json.dumps(gateway.calls)


def test_extraction_rejects_nonexistent_source_pointer(tmp_path):
    response = _responses()
    response[0]["proposals"][0]["source_path"] = "/general_liability/not_a_value"
    gateway = MockGateway(scripted=response)
    outcome = extract_document_exposures(
        gateway, _tracker(),
        Settings(_env_file=None, spectra_max_evidence_payload_chars=10000), _document())

    assert outcome.status == "INCOMPLETE"
    assert not outcome.exposures
    assert any("Source path does not resolve" in error for error in outcome.errors)
    assert len(gateway.calls) == 1  # invalid source never reaches the judge


def test_judge_rejection_prevents_model_integration():
    outcome = extract_document_exposures(
        MockGateway(scripted=_responses(verdict="REJECT", accepted=[])),
        _tracker(),
        Settings(_env_file=None, spectra_max_evidence_payload_chars=10000),
        _document(),
    )
    assert outcome.status == "REJECTED_BY_JUDGE"
    assert not outcome.exposures and not outcome.evidence
    assert outcome.errors


def test_extraction_does_not_accept_unsupported_units():
    response = _responses()
    response[0]["proposals"][0]["unit_path"] = "/general_liability/annual_sales"
    outcome = extract_document_exposures(
        MockGateway(scripted=response), _tracker(),
        Settings(_env_file=None, spectra_max_evidence_payload_chars=10000), _document())
    assert outcome.status == "INCOMPLETE"
    assert not outcome.exposures
    assert any("unit" in error for error in outcome.errors)


def test_extraction_expands_schema_paths_for_all_array_rows():
    source = CanonicalDocument(
        document_id="DOC-ROWS",
        document_type="unstructured_document",
        raw={"locations": [
            {"annual_sales": 10, "currency": "USD", "id": "L1"},
            {"annual_sales": 20, "currency": "USD", "id": "L2"},
        ]},
    )
    scripted = [
        {
            "proposals": [{
                "source_path": "/locations/*/annual_sales",
                "exposure_type": "sales",
                "unit_path": "/locations/*/currency",
                "period_path": None,
                "location_id_path": "/locations/*/id",
            }],
            "summary": "Sales by location.",
        },
        {
            "verdict": "PASS",
            "accepted_paths": ["/locations/*/annual_sales"],
            "issues": [],
            "summary": "Paths are complete.",
        },
    ]
    outcome = extract_document_exposures(
        MockGateway(scripted=scripted), _tracker(),
        Settings(_env_file=None, spectra_max_evidence_payload_chars=10000), source)

    assert outcome.status == "ACCEPTED"
    assert [item.value for item in outcome.exposures] == [10, 20]
    assert [item.location_id for item in outcome.exposures] == ["L1", "L2"]
    assert [item.source_location.json_path for item in outcome.evidence] == [
        "/locations/0/annual_sales", "/locations/1/annual_sales"]


def test_schema_view_includes_late_distinct_row_shapes_without_values():
    source = CanonicalDocument(
        document_id="DOC-SCHEMA",
        document_type="unstructured_document",
        raw={"rows": [
            {"amount": 1, "currency": "USD"},
            {"amount": 2, "currency": "USD"},
            {"amount": 3, "currency": "USD"},
            {"area": 4, "unit": "sqft", "account": "PRIVATE-ACCOUNT"},
        ]},
    )
    gateway = MockGateway(scripted=[{
        "proposals": [],
        "summary": "No proposals.",
    }])

    extract_document_exposures(
        gateway, _tracker(),
        Settings(_env_file=None, spectra_max_evidence_payload_chars=10000), source)
    prompt = gateway.calls[0]["user"]
    assert '"area"' in prompt and '"unit"' in prompt
    assert "PRIVATE-ACCOUNT" not in prompt
    assert '"amount"' in prompt and '"currency"' in prompt


def test_model_snapshot_is_versioned_and_excludes_raw_documents(tmp_path):
    case = make_case([envelope("acord_126", {
        "sales_rows": [{"amount": 2500, "location_id": "L1"}],
    })], submission_id="SAFE-ID")
    target = save_exposure_model(tmp_path, case)

    model = json.loads(target.read_text(encoding="utf-8"))
    assert model["schema_version"] == "1.0"
    assert model["submission_id"] == "SAFE-ID"
    assert model["documents"][0]["exposures"][0]["record"]["value"] == 2500
    assert all("raw" not in document for document in model["documents"])
    assert target.parent.parent.name != "SAFE-ID"
