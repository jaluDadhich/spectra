"""Unit tests for deterministic rules. Each test builds a minimal case and
runs the full default ruleset, filtering to the rule under test."""
from __future__ import annotations

import pytest

from app.catalog.profiles import default_profile
from app.rules import RuleEngine, load_registry
from app.schemas.findings import ConclusionState
from tests.conftest import envelope, make_case


@pytest.fixture(scope="module")
def engine():
    return RuleEngine(load_registry("default"), default_profile())


def run(engine, docs, **kw):
    case = make_case(docs, **kw)
    findings, checks, rejected = engine.run(case)
    return case, findings, checks, rejected


def by_rule(findings, prefix):
    return [f for f in findings if f.rule_id.startswith(prefix)]


def test_missing_required_document_is_evidence_gap(engine):
    _, findings, _, _ = run(engine, [envelope("acord_125", {"applicant": {"legal_name": "X"}})],
                            line_of_business="package", review_date="2026-10-28")
    doc_findings = by_rule(findings, "DOC-INVENTORY")
    assert any(f.finding_type == "MISSING_REQUIRED_DOCUMENT" for f in doc_findings)
    f = next(f for f in doc_findings if f.finding_type == "MISSING_REQUIRED_DOCUMENT")
    assert f.conclusion_state == ConclusionState.EVIDENCE_GAP
    assert "not proof" in (f.uncertainty_reason or "")


def test_p1_missing_roster_never_asserts_unqualified(engine):
    docs = [
        envelope("fleet_schedule", {"vehicles": [
            {"unit_id": "U1", "assigned_driver_id": "D1", "status": "active"}]}),
        envelope("submission_checklist", {"items": [
            {"item": "driver_roster", "status": "missing"}]}),
    ]
    _, findings, _, _ = run(engine, docs, line_of_business="package",
                            review_date="2026-10-28")
    p1 = by_rule(findings, "P1-")
    assert p1
    assert p1[0].conclusion_state == ConclusionState.EVIDENCE_GAP
    assert "unqualified" in p1[0].uncertainty_reason


def test_p3_fleet_count_delta_and_formula(engine):
    docs = [
        envelope("fleet_schedule", {"vehicles": [
            {"unit_id": "U1", "status": "active"},
            {"unit_id": "U2", "status": "active"},
            {"unit_id": "U3", "status": "sold"}]}),
        envelope("quote", {"rating_inputs": {"vehicle_count": 5}, "terms": []}),
    ]
    _, findings, _, _ = run(engine, docs, line_of_business="package")
    p3 = by_rule(findings, "P3-FLEET")
    assert len(p3) == 1
    cmp = p3[0].comparison
    assert cmp.delta == 3 and cmp.delta_formula
    assert cmp.values_compared == [5.0, 2]


def test_p3_wc_payroll_within_tolerance_no_finding(engine):
    docs = [
        envelope("acord_130", {"class_rows": [{"class_code": "8810", "state": "CA",
                                               "payroll": 1000000}]}),
        envelope("wc_rating_workbook", {"payroll_rows": [
            {"class_code": "8810", "state": "CA", "payroll": 1005000}]}),  # 0.5% < 1% tol
    ]
    _, findings, _, _ = run(engine, docs, line_of_business="workers_comp")
    assert not by_rule(findings, "P3-WC")


def test_p5_duplicate_claim_and_incurred_recalc(engine):
    docs = [envelope("loss_run", {
        "valuation_date": "2026-10-01",
        "claims": [
            {"claim_id": "C1", "loss_date": "2025-01-10", "paid": 100, "reserves": 50,
             "incurred": 150, "policy_year": "2025"},
            {"claim_id": "C1", "loss_date": "2025-01-10", "paid": 100, "reserves": 90,
             "incurred": 190, "policy_year": "2025"},
            {"claim_id": "C2", "loss_date": "2025-02-01", "paid": 100, "reserves": 50,
             "incurred": 300, "policy_year": "2025"},  # 300 != 150
        ],
        "summary": {"total_incurred": 490, "claim_count": 3,
                    "annual_counts": [{"year": "2025", "count": 3}]},
    })]
    _, findings, _, _ = run(engine, docs, line_of_business="package")
    p5 = by_rule(findings, "P5-")
    types = {f.finding_type for f in p5}
    assert "DUPLICATE_CLAIM_CONFLICT" in types
    assert "INCURRED_CALC_MISMATCH" in types
    dup = next(f for f in p5 if f.finding_type == "DUPLICATE_CLAIM_CONFLICT")
    assert len(dup.evidence) >= 4  # both source rows cited


def test_n5_all_lines_history_is_not_mislabeled_as_gl(engine):
    docs = [
        envelope("loss_run", {"claims": [
            {"claim_id": "G1", "line_of_business": "GL", "loss_date": "2025-01-01",
             "policy_year": "2025"},
            {"claim_id": "W1", "line_of_business": "WC", "loss_date": "2025-02-01",
             "policy_year": "2025"},
        ]}),
        envelope("acord_126", {"claims_history": [{
            "year": "2025", "claim_count_all_lines": 2,
        }]}),
    ]
    case, findings, _, _ = run(engine, docs, line_of_business="package")
    history = next(exp for exp in case.exposures
                   if exp.exposure_type == "annual_claim_count")
    assert history.description == "all lines"
    assert not by_rule(findings, "N5-ANNUAL-COUNTS")


def test_n5_does_not_compare_unspecified_lob_to_all_loss_detail(engine):
    docs = [
        envelope("loss_run", {"claims": [
            {"claim_id": "G1", "line_of_business": "GL", "loss_date": "2025-01-01",
             "policy_year": "2025"},
            {"claim_id": "W1", "line_of_business": "WC", "loss_date": "2025-02-01",
             "policy_year": "2025"},
        ]}),
        envelope("acord_126", {"claims_history": [{
            "year": "2025", "claim_count": 1,
        }]}),
    ]
    _, findings, _, _ = run(engine, docs, line_of_business="package")
    assert not by_rule(findings, "N5-ANNUAL-COUNTS")


def test_p6_mismatch_requires_validation_without_authority(engine):
    docs = [
        envelope("acord_130", {"experience_mod": {"factor": 0.90,
                                                  "effective_date": "2026-01-01"}}),
        envelope("quote", {"rating_inputs": {"experience_mod": {
            "factor": 1.05, "effective_date": "2026-01-01"}}}),
    ]
    _, findings, _, _ = run(engine, docs, line_of_business="workers_comp",
                            effective_date="2026-11-15")
    p6 = by_rule(findings, "P6-")
    assert len(p6) == 1
    assert p6[0].conclusion_state == ConclusionState.POSSIBLE_MISMATCH
    assert "requires validation" in (p6[0].possible_consequence + (p6[0].uncertainty_reason or "")) \
        or "cannot be determined" in p6[0].uncertainty_reason


def test_p6_no_finding_when_consistent(engine):
    docs = [
        envelope("acord_130", {"experience_mod": {"factor": 0.95,
                                                  "effective_date": "2026-01-01"}}),
        envelope("quote", {"rating_inputs": {"experience_mod": {
            "factor": 0.95, "effective_date": "2026-01-01"}}}),
    ]
    _, findings, _, _ = run(engine, docs, line_of_business="workers_comp")
    assert not by_rule(findings, "P6-")


def test_p4_explained_version_change_no_finding(engine):
    v1 = envelope("quote", {"terms": []}, doc_id="Q1", version="received")
    v2 = envelope("quote", {"terms": [{"term_type": "limit", "value": 5}]},
                  doc_id="Q2", version="resubmitted")
    v2["data"]["change_log"] = "corrected limit per insured request"
    _, findings, _, _ = run(engine, [v1, v2], line_of_business="package")
    assert not by_rule(findings, "P4-")


def test_p4_unexplained_difference_flagged(engine):
    v1 = envelope("quote", {"terms": []}, doc_id="Q1", version="received")
    v2 = envelope("quote", {"terms": [{"term_type": "limit", "value": 5}]},
                  doc_id="Q2", version="resubmitted")
    _, findings, _, _ = run(engine, [v1, v2], line_of_business="package")
    p4 = by_rule(findings, "P4-")
    assert p4 and p4[0].conclusion_state == ConclusionState.POSSIBLE_MISMATCH
    assert "not evidence of manipulation" in p4[0].uncertainty_reason


def test_n7_garaging_conflict(engine):
    docs = [envelope("fleet_schedule", {"vehicles": [
        {"unit_id": "U1", "garaging_state": "CA",
         "garaging_address": "10 Main St, Reno, NV 89501", "status": "active"}]})]
    _, findings, _, _ = run(engine, docs, line_of_business="commercial_auto")
    n7 = by_rule(findings, "N7-")
    assert len(n7) == 1
    assert "territory" in n7[0].possible_consequence.lower()


def test_n8_absent_roster_is_gap_not_mismatch(engine):
    docs = [envelope("fleet_schedule", {"vehicles": [
        {"unit_id": "U1", "gvwr": 33000, "status": "active",
         "assigned_driver_id": "D1"}]})]
    _, findings, _, _ = run(engine, docs, line_of_business="commercial_auto")
    n8 = by_rule(findings, "N8-")
    assert n8[0].conclusion_state == ConclusionState.EVIDENCE_GAP


def test_n8_light_vehicles_no_finding(engine):
    docs = [envelope("fleet_schedule", {"vehicles": [
        {"unit_id": "U1", "gvwr": 10000, "status": "active"}]})]
    _, findings, _, _ = run(engine, docs, line_of_business="commercial_auto")
    assert not by_rule(findings, "N8-")


def test_n15_rate_discrepancy_never_picks_winner(engine):
    docs = [
        envelope("wc_rating_workbook", {"rate_tables": [
            {"class_code": "8810", "state": "CA", "rate": 1.25}]}),
        envelope("quote", {"rating_inputs": {"class_rates": [
            {"class_code": "8810", "state": "CA", "rate": 1.60}]}}),
    ]
    _, findings, _, _ = run(engine, docs, line_of_business="workers_comp")
    n15 = by_rule(findings, "N15-")
    assert len(n15) == 1
    assert n15[0].conclusion_state == ConclusionState.POSSIBLE_MISMATCH
    assert "not authenticated rate support" in n15[0].uncertainty_reason


def test_dq_date_ordering(engine):
    bad_period = envelope("acord_125", {"applicant": {"legal_name": "X"}},
                          effective_period={"start": "2027-01-01", "end": "2026-01-01"})
    _, findings, _, _ = run(engine, [bad_period], line_of_business="package")
    assert any(f.finding_type == "INVALID_DATE_ORDER" for f in by_rule(findings, "DQ-DATES"))


def test_stale_loss_run_flagged(engine):
    docs = [envelope("loss_run", {"claims": []}, received_at="2026-01-01T00:00:00Z"),
            envelope("quote", {"terms": []})]
    _, findings, _, _ = run(engine, docs, line_of_business="package",
                            review_date="2026-10-28")
    stale = [f for f in by_rule(findings, "DQ-STALE")
             if "loss" in f.title.lower()]
    assert stale and stale[0].conclusion_state == ConclusionState.EVIDENCE_GAP


def test_p11_limitation_when_wording_missing(engine):
    docs = [
        envelope("ops_supplement", {"peak_shipment_value": 250000}),
        envelope("quote", {"terms": [
            {"term_type": "limit", "coverage": "cargo", "value": 100000}]}),
    ]
    _, findings, _, _ = run(engine, docs, line_of_business="package")
    p11 = by_rule(findings, "P11-")
    assert len(p11) == 1
    assert "wording not supplied" in p11[0].possible_consequence
    assert p11[0].comparison.delta == 150000


def test_rule_failure_becomes_failed_check(engine, monkeypatch):
    import app.rules.handlers as h

    def boom(ctx):
        raise RuntimeError("boom")

    monkeypatch.setitem(engine.registry.handlers, "p1_driver_roster", boom)
    _, _, checks, _ = run(engine, [envelope("quote", {"terms": []})],
                          line_of_business="package")
    failed = [c for c in checks if c.check_id == "P1-DRIVER-ROSTER-001"]
    assert failed and failed[0].status.value == "FAILED"
