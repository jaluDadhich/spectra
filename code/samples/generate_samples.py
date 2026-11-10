"""Synthetic fixture generator for Spectra tests and evaluation.

ALL DATA IS SYNTHETIC. Companies, people, VINs, claims, and records are
fabricated for testing and do not describe real entities.

Run:  python samples/generate_samples.py
Produces: samples/clean/, samples/a01/, samples/a02/, samples/a03/
and evaluation/ground_truth/{a01,a02,a03}.json (isolated from runtime).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
GT = ROOT / "evaluation" / "ground_truth"

RECEIVED = "2026-10-20T10:00:00Z"
PERIOD = {"start": "2026-11-15", "end": "2027-11-15"}


def env(submission_id: str, doc_id: str, doc_type: str, name: str, data: dict,
        version: str = "received", provenance_fields: dict | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "submission_id": submission_id,
        "document": {
            "document_id": doc_id,
            "document_type": doc_type,
            "document_name": name,
            "issuer": "broker_synthetic",
            "version": version,
            "received_at": RECEIVED,
            "effective_period": PERIOD,
            "source_hash": None,
            "source_metadata": {},
        },
        "data": data,
        "provenance": {
            "extraction_source": "synthetic_fixture_generator",
            "source_file": name.replace(".xlsx", ".json"),
            "fields": provenance_fields or {},
        },
    }


def base_documents(sub: str) -> dict[str, dict]:
    """The internally consistent 'clean' submission."""
    docs: dict[str, dict] = {}
    docs["acord125.json"] = env(sub, "A-ACORD125", "acord_125", "ACORD_125.pdf", {
        "applicant": {
            "legal_name": "Sierra Pacific Logistics LLC",
            "dba": ["Sierra Pacific"],
            "fein": "47-2210983",
            "usdot_number": "3847291",
        },
        "locations": [
            {"location_id": "LOC-1", "address": "100 Warehouse Way", "city": "Fresno",
             "state": "CA", "zip": "93721", "square_footage": 40000,
             "building_value": 3000000, "contents_value": 1000000},
            {"location_id": "LOC-2", "address": "250 Depot Road", "city": "Sacramento",
             "state": "CA", "zip": "95814", "square_footage": 15000,
             "building_value": 1500000, "contents_value": 500000},
        ],
        "operations_description": "Warehousing and local delivery operations from two California locations.",
        "gross_sales": 8000000,
        "sales_period": "annual",
        "employee_counts": {"full_time": 20, "part_time": 5},
    })
    docs["acord126.json"] = env(sub, "A-ACORD126", "acord_126", "ACORD_126.pdf", {
        "sales_rows": [
            {"row_id": "S1", "location_id": "LOC-1", "description": "warehousing", "amount": 5000000},
            {"row_id": "S2", "location_id": "LOC-2", "description": "distribution", "amount": 3000000},
        ],
        "area_rows": [
            {"row_id": "AR1", "location_id": "LOC-1", "square_footage": 40000},
            {"row_id": "AR2", "location_id": "LOC-2", "square_footage": 15000},
        ],
        "claims_history": [{"year": "2025", "claim_count": 1, "line_of_business": "GL"}],
    })
    docs["acord130.json"] = env(sub, "A-ACORD130", "acord_130", "ACORD_130.pdf", {
        "states": ["CA"],
        "class_rows": [
            {"row_id": "W1", "class_code": "8810", "state": "CA", "location_id": "LOC-1",
             "payroll": 1200000, "full_time": 12, "part_time": 3},
            {"row_id": "W2", "class_code": "7219", "state": "CA", "location_id": "LOC-2",
             "payroll": 800000, "full_time": 8, "part_time": 2},
        ],
        "experience_mod": {"factor": 0.95, "effective_date": "2026-01-01"},
    })
    docs["acord140.json"] = env(sub, "A-ACORD140", "acord_140", "ACORD_140.pdf", {
        "premises": [
            {"location_id": "LOC-1", "address": "100 Warehouse Way", "city": "Fresno",
             "state": "CA", "zip": "93721", "square_footage": 40000,
             "building_value": 3000000, "contents_value": 1000000},
            {"location_id": "LOC-2", "address": "250 Depot Road", "city": "Sacramento",
             "state": "CA", "zip": "95814", "square_footage": 15000,
             "building_value": 1500000, "contents_value": 500000},
        ],
    })
    docs["sov.json"] = env(sub, "A-SOV", "sov", "SOV.xlsx", {
        "locations": [
            {"location_id": "LOC-1", "address": "100 Warehouse Way", "city": "Fresno",
             "state": "CA", "zip": "93721", "building_value": 3000000,
             "contents_value": 1000000, "tiv": 4000000},
            {"location_id": "LOC-2", "address": "250 Depot Road", "city": "Sacramento",
             "state": "CA", "zip": "95814", "building_value": 1500000,
             "contents_value": 500000, "tiv": 2000000},
        ],
    }, provenance_fields={
        "locations[0].tiv": {"source_sheet": "SOV", "source_row": 2, "source_column": "H",
                             "source_cell": "H2", "extraction_confidence": 0.99},
        "locations[1].tiv": {"source_sheet": "SOV", "source_row": 3, "source_column": "H",
                             "source_cell": "H3", "extraction_confidence": 0.99},
    })
    docs["wc_workbook.json"] = env(sub, "A-WCWB", "wc_rating_workbook", "WC_Rating_Workbook.xlsx", {
        "payroll_rows": [
            {"row_id": "R1", "class_code": "8810", "state": "CA", "location_id": "LOC-1",
             "payroll": 1200000, "rate": 1.25, "full_time": 12, "part_time": 3},
            {"row_id": "R2", "class_code": "7219", "state": "CA", "location_id": "LOC-2",
             "payroll": 800000, "rate": 4.50, "full_time": 8, "part_time": 2},
        ],
        "experience_mod_history": [
            {"factor": 0.98, "effective_date": "2025-01-01", "source": "bureau"},
            {"factor": 0.95, "effective_date": "2026-01-01", "source": "bureau"},
        ],
        "current_experience_mod": {"factor": 0.95, "effective_date": "2026-01-01",
                                   "source": "workbook"},
        "declared_totals": {"total_payroll": 2000000},
        "rate_tables": [
            {"class_code": "8810", "state": "CA", "rate": 1.25, "effective_date": "2026-01-01"},
            {"class_code": "7219", "state": "CA", "rate": 4.50, "effective_date": "2026-01-01"},
        ],
    }, provenance_fields={
        "payroll_rows[0].payroll": {"source_sheet": "Payroll by Class", "source_row": 5,
                                    "source_column": "D", "source_cell": "D5",
                                    "extraction_confidence": 0.99},
        "payroll_rows[1].payroll": {"source_sheet": "Payroll by Class", "source_row": 6,
                                    "source_column": "D", "source_cell": "D6",
                                    "extraction_confidence": 0.99},
    })
    docs["loss_run.json"] = env(sub, "A-LOSSRUN", "loss_run", "Loss_Run.pdf", {
        "valuation_date": "2026-10-01",
        "claims": [
            {"claim_id": "CLM-2001", "line_of_business": "GL", "loss_date": "2025-03-14",
             "paid": 50000, "reserves": 25000, "incurred": 75000, "status": "open",
             "policy_year": "2025"},
            {"claim_id": "CLM-2002", "line_of_business": "WC", "loss_date": "2025-06-02",
             "paid": 10000, "reserves": 0, "incurred": 10000, "status": "closed",
             "policy_year": "2025"},
        ],
        "summary": {"total_incurred": 85000, "claim_count": 2,
                    "annual_counts": [{"year": "2025", "count": 2}]},
    })
    docs["fleet_schedule.json"] = env(sub, "A-FLEET", "fleet_schedule", "Fleet_Schedule.xlsx", {
        "vehicles": [
            {"unit_id": "U-1", "vin": "1FTYR10U33PA12345", "status": "active",
             "gvwr": 33000, "vehicle_type": "tractor",
             "garaging_state": "CA", "garaging_address": "100 Warehouse Way, Fresno, CA 93721",
             "assigned_driver_id": "D1", "cost_new": 180000},
            {"unit_id": "U-2", "vin": "1GBJC34R82F11223", "status": "active",
             "gvwr": 14000, "vehicle_type": "box_truck",
             "garaging_state": "CA", "garaging_address": "250 Depot Road, Sacramento, CA 95814",
             "assigned_driver_id": "D2", "cost_new": 65000},
            {"unit_id": "U-3", "vin": "2HGFC2F59MH54321", "status": "sold",
             "gvwr": 12000, "vehicle_type": "van",
             "garaging_state": "CA", "garaging_address": "250 Depot Road, Sacramento, CA 95814",
             "assigned_driver_id": None, "cost_new": 42000},
        ],
    })
    docs["driver_roster.json"] = env(sub, "A-ROSTER", "driver_roster", "Driver_Roster.xlsx", {
        "roster_date": "2026-09-15",
        "drivers": [
            {"driver_id": "D1", "name": "Maria Torres", "license_class": "CDL-A",
             "cdl_endorsements": ["T"], "mvr_review_date": "2026-08-01",
             "hire_date": "2022-04-11"},
            {"driver_id": "D2", "name": "James Chen", "license_class": "CDL-B",
             "cdl_endorsements": [], "mvr_review_date": "2026-08-01",
             "hire_date": "2023-02-20"},
        ],
    })
    docs["quote.json"] = env(sub, "A-QUOTE", "quote", "Draft_Quote.pdf", {
        "terms": [
            {"term_id": "Q-GL-OCC", "term_type": "limit", "coverage": "general_liability",
             "name": "each_occurrence", "value": 1000000},
            {"term_id": "Q-PROP-BLANKET", "term_type": "limit", "coverage": "property",
             "name": "blanket_building_contents", "value": 6000000},
            {"term_id": "Q-PROP-DED", "term_type": "deductible", "coverage": "property",
             "name": "per_occurrence", "value": 5000},
            {"term_id": "Q-FORM-CP", "term_type": "form", "coverage": "property",
             "name": "CP 00 10", "value": "CP 00 10 10 12"},
        ],
        "rating_inputs": {
            "payroll_by_class": [
                {"class_code": "8810", "state": "CA", "payroll": 1200000},
                {"class_code": "7219", "state": "CA", "payroll": 800000},
            ],
            "experience_mod": {"factor": 0.95, "effective_date": "2026-01-01"},
            "class_rates": [
                {"class_code": "8810", "state": "CA", "rate": 1.25, "effective_date": "2026-01-01"},
                {"class_code": "7219", "state": "CA", "rate": 4.50, "effective_date": "2026-01-01"},
            ],
            "vehicle_count": 2,
            "gross_sales": 8000000,
        },
        "statements": {"driver_roster_attached": True, "loss_runs_attached": True},
        "operations_described": ["warehousing", "local delivery"],
    })
    docs["broker_email.json"] = env(sub, "A-EMAIL", "broker_email", "Broker_Email.eml", {
        "subject": "Renewal submission - Sierra Pacific Logistics",
        "body": "Please find the renewal submission for Sierra Pacific Logistics. "
                "Operations are warehousing and local delivery from two California locations.",
    })
    docs["checklist.json"] = env(sub, "A-CHECKLIST", "submission_checklist", "Checklist.xlsx", {
        "items": [
            {"item": "acord_forms", "status": "attached"},
            {"item": "loss_run", "status": "attached"},
            {"item": "driver_roster", "status": "attached"},
        ],
    })
    return docs


# ----------------------------------------------------------------------
# Account mutations. Every planted issue mirrors the documented taxonomy.
# ----------------------------------------------------------------------

def mutate_a01(docs: dict[str, dict]) -> None:
    """Manifested P2, P3, P6. Non-manifested N3, N4, N5, N6, N7, N8, N14, N15."""
    # P2 — SOV lists an extra location not on the application
    docs["sov.json"]["data"]["locations"].append(
        {"location_id": "LOC-3", "address": "88 Industrial Pkwy", "city": "Reno",
         "state": "NV", "zip": "89502", "building_value": 900000,
         "contents_value": 300000, "tiv": 1200000})
    # P3-WC-PAYROLL — workbook payroll disagrees with application/quote
    docs["wc_workbook.json"]["data"]["payroll_rows"][1]["payroll"] = 950000
    docs["wc_workbook.json"]["data"]["declared_totals"]["total_payroll"] = 2300000
    # N3 — workbook allocates payroll to NV, absent from ACORD 130 schedule
    docs["wc_workbook.json"]["data"]["payroll_rows"].append(
        {"row_id": "R3", "class_code": "8810", "state": "NV", "location_id": "LOC-3",
         "payroll": 150000, "rate": 1.30})
    docs["wc_workbook.json"]["data"]["declared_totals"]["total_payroll"] = 2300000
    # P6 — experience mod disagreement (no authoritative current source supplied)
    docs["quote.json"]["data"]["rating_inputs"]["experience_mod"] = {
        "factor": 1.10, "effective_date": "2026-01-01"}
    # N4 — employee counts differ between ACORD 125 and WC docs
    docs["acord125.json"]["data"]["employee_counts"]["full_time"] = 22
    # N5 — loss summary annual count disagrees with loss detail
    docs["loss_run.json"]["data"]["summary"]["annual_counts"] = [{"year": "2025", "count": 3}]
    # N6 — GL sales rows do not reconcile to declared gross sales
    docs["acord126.json"]["data"]["sales_rows"][1]["amount"] = 1500000
    # N7 — garaging state code conflicts with garaging address
    docs["fleet_schedule.json"]["data"]["vehicles"][1]["garaging_address"] = (
        "400 Terminal Ave, Reno, NV 89502")
    # N8 — heavy vehicle assigned to driver without CDL-class evidence
    docs["driver_roster.json"]["data"]["drivers"][0]["license_class"] = "C"
    # N14 — OSHA record name-matches only; premises unresolved
    docs["external_records.json"] = env("SPX-A01-2026-0101", "A-EXT", "external_records",
                                        "External_Research.xlsx", {
        "observations": [{
            "record_id": "OSHA-4471", "source_name": "OSHA",
            "subject": "inspection", "entity_name": "Sierra Pacific Logistics Inc",
            "address": "5000 Other Blvd", "state": "CA",
            "record_date": "2025-05-11", "retrieved_at": "2026-10-18",
            "record_identifier": "osha-inspection-4471",
        }],
    })
    # N15 — quote rate disagrees with workbook rate table
    docs["quote.json"]["data"]["rating_inputs"]["class_rates"][0]["rate"] = 1.60


def mutate_a02(docs: dict[str, dict]) -> None:
    """Manifested P2, P4, P5, P7. Non-manifested N3, N4, N6, N7, N8, N14, N15.
    N5 is NOT a separate finding (no independent annual-count evidence)."""
    # P2 — SOV value conflict at LOC-2
    docs["sov.json"]["data"]["locations"][1]["tiv"] = 2600000
    docs["sov.json"]["data"]["locations"][1]["building_value"] = 2100000
    # P4 — resubmitted ACORD 126 with unexplained material change
    resub = copy.deepcopy(docs["acord126.json"])
    resub["document"]["document_id"] = "A-ACORD126-V2"
    resub["document"]["version"] = "resubmitted"
    resub["data"]["sales_rows"][0]["amount"] = 5200000
    resub["data"]["sales_rows"][1]["amount"] = 3300000
    docs["acord126_resubmitted.json"] = resub
    # (N6 fires as a consequence of the unreconciled sales bases — expected for A02)
    # P5 — duplicate claim ID with conflicting financials
    docs["loss_run.json"]["data"]["claims"].insert(1, {
        "claim_id": "CLM-2001", "line_of_business": "GL", "loss_date": "2025-03-14",
        "paid": 50000, "reserves": 45000, "incurred": 95000, "status": "open",
        "policy_year": "2025"})
    docs["loss_run.json"]["data"]["summary"] = {
        "total_incurred": 180000, "claim_count": 3,
        "annual_counts": [{"year": "2025", "count": 3}]}
    docs["acord126.json"]["data"]["claims_history"] = [
        {"year": "2025", "claim_count": 3, "line_of_business": "GL"}]
    resub["data"]["claims_history"] = [{"year": "2025", "claim_count": 3, "line_of_business": "GL"}]
    # P7 + N14 — external record describes undeclared operations; name-only identity
    docs["external_records.json"] = env("SPX-A02-2026-0202", "A-EXT", "external_records",
                                        "External_Research.xlsx", {
        "observations": [{
            "record_id": "OSHA-9921", "source_name": "OSHA",
            "subject": "inspection", "entity_name": "Sierra Pacific Logistics LLC",
            "address": "771 Unknown Plaza", "state": "CA",
            "record_date": "2025-09-02", "retrieved_at": "2026-10-18",
            "record_identifier": "osha-inspection-9921",
            "services": ["warehousing", "snow removal"],
        }],
    })
    # N3 — SOV lists an NV premises with no WC payroll rows (qualified signal;
    # payroll stays reconciled so P3 is correctly absent for A02)
    docs["sov.json"]["data"]["locations"].append(
        {"location_id": "LOC-9", "address": "77 Distribution Ct", "city": "Sparks",
         "state": "NV", "zip": "89431", "building_value": 500000,
         "contents_value": 100000, "tiv": 600000})
    # N4 — counts differ
    docs["acord125.json"]["data"]["employee_counts"]["full_time"] = 24
    # N7 — garaging conflict
    docs["fleet_schedule.json"]["data"]["vehicles"][0]["garaging_state"] = "NV"
    # N8 — heavy vehicle assigned to a driver not on the roster
    docs["fleet_schedule.json"]["data"]["vehicles"][0]["assigned_driver_id"] = "D9"
    # N15 — rate disagreement
    docs["quote.json"]["data"]["rating_inputs"]["class_rates"][1]["rate"] = 5.10
    # P6 consistency: keep all factors 0.95 (P6 not expected for A02)


def mutate_a03(docs: dict[str, dict]) -> None:
    """Manifested P1, P3, P6, P7, P11. Non-manifested N3, N4, N5, N6, N7, N8, N15.
    N14 excluded (identity/scope covered by P7); N8 is an evidence gap."""
    # P1 — no driver roster supplied; checklist and quote say so
    del docs["driver_roster.json"]
    docs["checklist.json"]["data"]["items"][2]["status"] = "missing"
    docs["quote.json"]["data"]["statements"]["driver_roster_attached"] = False
    # P3-FLEET-COUNT — quote declares 12 power units vs 2 active on schedule
    docs["quote.json"]["data"]["rating_inputs"]["vehicle_count"] = 12
    # P3-WC-PAYROLL — workbook vs application mismatch
    docs["wc_workbook.json"]["data"]["payroll_rows"][0]["payroll"] = 1350000
    docs["wc_workbook.json"]["data"]["declared_totals"]["total_payroll"] = 2300000
    # P6 — emod mismatch
    docs["acord130.json"]["data"]["experience_mod"] = {"factor": 0.90,
                                                       "effective_date": "2026-01-01"}
    docs["wc_workbook.json"]["data"]["current_experience_mod"]["factor"] = 1.02
    docs["quote.json"]["data"]["rating_inputs"]["experience_mod"]["factor"] = 1.02
    # P7 — external record (premises-matched to LOC-1) lists undeclared service
    docs["external_records.json"] = env("SPX-A03-2026-1042", "A-EXT", "external_records",
                                        "External_Research.xlsx", {
        "observations": [{
            "record_id": "PUB-118", "source_name": "OSHA",
            "subject": "inspection", "entity_name": "Sierra Pacific Logistics LLC",
            "address": "100 Warehouse Way", "city": "Fresno", "state": "CA", "zip": "93721",
            "record_date": "2026-02-14", "retrieved_at": "2026-10-18",
            "record_identifier": "osha-inspection-118",
            "services": ["warehousing", "hazardous materials hauling"],
        }],
    })
    # P11 — peak shipment value exceeds quoted cargo limit
    docs["ops_supplement.json"] = env("SPX-A03-2026-1042", "A-OPS", "ops_supplement",
                                      "Operations_Supplement.pdf", {
        "operations": [{"description": "warehousing and local delivery",
                        "location_id": "LOC-1"}],
        "peak_shipment_value": 250000,
        "cargo_description": "customer goods in transit",
    })
    docs["quote.json"]["data"]["terms"].append(
        {"term_id": "Q-CARGO", "term_type": "limit", "coverage": "cargo",
         "name": "cargo_per_conveyance", "value": 100000})
    # N3 — workbook state not on ACORD 130
    docs["wc_workbook.json"]["data"]["payroll_rows"].append(
        {"row_id": "R3", "class_code": "8810", "state": "AZ", "location_id": "LOC-4",
         "payroll": 90000, "rate": 1.30})
    docs["wc_workbook.json"]["data"]["declared_totals"]["total_payroll"] = 2390000
    # N4 — counts differ
    docs["acord125.json"]["data"]["employee_counts"]["full_time"] = 25
    # N5 — annual count mismatch
    docs["loss_run.json"]["data"]["summary"]["annual_counts"] = [{"year": "2025", "count": 4}]
    # N6 — sales rows do not reconcile
    docs["acord126.json"]["data"]["sales_rows"][0]["amount"] = 4200000
    # N7 — garaging conflict
    docs["fleet_schedule.json"]["data"]["vehicles"][1]["garaging_state"] = "OR"
    # N15 — rate disagreement
    docs["quote.json"]["data"]["rating_inputs"]["class_rates"][0]["rate"] = 1.75


GROUND_TRUTH = {
    "a01": {
        "account": "A01",
        "expected": [
            {"taxonomy_id": "P2", "manifested": True, "note": "SOV extra location vs application"},
            {"taxonomy_id": "P3", "manifested": True, "note": "WC payroll mismatch"},
            {"taxonomy_id": "P6", "manifested": True, "note": "experience mod mismatch"},
            {"taxonomy_id": "N3", "manifested": False}, {"taxonomy_id": "N4", "manifested": False},
            {"taxonomy_id": "N5", "manifested": False}, {"taxonomy_id": "N6", "manifested": False},
            {"taxonomy_id": "N7", "manifested": False}, {"taxonomy_id": "N8", "manifested": False},
            {"taxonomy_id": "N14", "manifested": False}, {"taxonomy_id": "N15", "manifested": False},
        ],
        "excluded": ["P1", "P4", "P5", "P7", "P11"],
    },
    "a02": {
        "account": "A02",
        "expected": [
            {"taxonomy_id": "P2", "manifested": True, "note": "SOV value conflict"},
            {"taxonomy_id": "P4", "manifested": True, "note": "unexplained resubmission"},
            {"taxonomy_id": "P5", "manifested": True, "note": "duplicate claim conflict"},
            {"taxonomy_id": "P7", "manifested": True, "note": "undeclared operations"},
            {"taxonomy_id": "N3", "manifested": False}, {"taxonomy_id": "N4", "manifested": False},
            {"taxonomy_id": "N6", "manifested": False}, {"taxonomy_id": "N7", "manifested": False},
            {"taxonomy_id": "N8", "manifested": False}, {"taxonomy_id": "N14", "manifested": False},
            {"taxonomy_id": "N15", "manifested": False},
        ],
        "excluded": ["P1", "P3", "P6", "P11", "N5"],
    },
    "a03": {
        "account": "A03",
        "expected": [
            {"taxonomy_id": "P1", "manifested": True, "note": "no driver roster"},
            {"taxonomy_id": "P3", "manifested": True, "note": "fleet count + payroll"},
            {"taxonomy_id": "P6", "manifested": True, "note": "emod mismatch"},
            {"taxonomy_id": "P7", "manifested": True, "note": "undeclared operations"},
            {"taxonomy_id": "P11", "manifested": True, "note": "cargo exposure exceeds limit"},
            {"taxonomy_id": "N3", "manifested": False}, {"taxonomy_id": "N4", "manifested": False},
            {"taxonomy_id": "N5", "manifested": False}, {"taxonomy_id": "N6", "manifested": False},
            {"taxonomy_id": "N7", "manifested": False}, {"taxonomy_id": "N8", "manifested": False},
            {"taxonomy_id": "N15", "manifested": False},
        ],
        "excluded": ["P2", "P4", "P5", "N14"],
    },
}


def write_account(name: str, submission_id: str, mutate) -> None:
    docs = base_documents(submission_id)
    if mutate:
        mutate(docs)
    out_dir = SAMPLES / name
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in docs.items():
        (out_dir / filename).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {len(docs)} documents -> {out_dir}")


def main() -> None:
    write_account("clean", "SPX-CLEAN-2026-0001", None)
    write_account("a01", "SPX-A01-2026-0101", mutate_a01)
    write_account("a02", "SPX-A02-2026-0202", mutate_a02)
    write_account("a03", "SPX-A03-2026-1042", mutate_a03)
    GT.mkdir(parents=True, exist_ok=True)
    for name, gt in GROUND_TRUTH.items():
        (GT / f"{name}.json").write_text(json.dumps(gt, indent=2), encoding="utf-8")
        print(f"wrote ground truth -> {GT / (name + '.json')}")


if __name__ == "__main__":
    main()
