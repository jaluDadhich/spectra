# Input JSON schemas and canonical model

## Envelope (preferred shape)

Each input file is one JSON object. The preferred envelope:

```json
{
  "schema_version": "1.0",
  "submission_id": "SPX-A03-2026-1042",
  "document": {
    "document_id": "A03-WC-RATING-WORKBOOK",
    "document_type": "wc_rating_workbook",
    "document_name": "A03_WC_Rating_Workbook.xlsx",
    "issuer": "broker",
    "version": "received",
    "received_at": "2026-10-28T10:00:00Z",
    "effective_period": {"start": "2026-11-15", "end": null},
    "source_hash": null,
    "source_metadata": {}
  },
  "data": { "...": "document-specific structured content" },
  "provenance": {
    "extraction_source": "upstream_json",
    "source_file": "A03_WC_Rating_Workbook.json",
    "fields": {
      "payroll_rows[0].payroll": {
        "source_sheet": "Payroll by Class", "source_row": 5,
        "source_column": "D", "source_cell": "D5",
        "source_page": null, "source_span": null,
        "extraction_confidence": 0.99
      }
    }
  }
}
```

A **bare JSON object** (no envelope) is also accepted: it is wrapped with
`extraction_source="bare_json"` and no source locations are claimed.
Unknown `document_type` values are preserved and reported as
`unsupported` — never silently discarded. If `document_type` is absent, the
registry sniffs the type from content keys (`claims`, `vehicles`,
`payroll_rows`, …). Provenance keys are data-relative JSON paths
(e.g. `payroll_rows[0].payroll`). If a field has no provenance entry, its
evidence records `available=false` and only the JSON path — page/cell
citations are never fabricated. Unsupported declared types are surfaced as an
incomplete inventory check rather than as an out-of-taxonomy underwriting
finding.

## Document types and `data` shapes

Field lookups are alias-tolerant (e.g. `class_code|class`,
`payroll|estimated_payroll`). Documented shapes:

| document_type | Key `data` sections |
|---|---|
| `acord_125` | `applicant{legal_name,dba[],fein,usdot_number,parent}`, `locations[]`, `operations_description`, `gross_sales`, `employee_counts{full_time,part_time}` |
| `acord_126` | `sales_rows[]{location_id,amount,description}`, `area_rows[]{location_id,square_footage}`, `claims_history[]{year,claim_count,line_of_business}` |
| `acord_130` | `states[]`, `class_rows[]{class_code,state,location_id,payroll,full_time,part_time}`, `experience_mod{factor,effective_date}` |
| `acord_140` | `premises[]{location_id,address,city,state,zip,building_value,contents_value,square_footage}` |
| `sov` | `locations[]{…,tiv}` |
| `wc_rating_workbook` | `payroll_rows[]{class_code,state,location_id,payroll,rate}`, `experience_mod_history[]`, `current_experience_mod{}`, `declared_totals{total_payroll}`, `rate_tables[]{class_code,state,rate,effective_date}` |
| `loss_run` | `valuation_date`, `claims[]{claim_id,line_of_business,loss_date,paid,reserves,incurred,status,policy_year}`, `summary{total_incurred,claim_count,annual_counts[]}` |
| `fleet_schedule` | `vehicles[]{unit_id,vin,status,gvwr,vehicle_type,garaging_state,garaging_address,assigned_driver_id,cost_new}` |
| `driver_roster` | `roster_date`, `drivers[]{driver_id,name,license_class,cdl_endorsements[],mvr_review_date,hire_date}` |
| `quote` | `terms[]{term_type(limit|deductible|sublimit|form|endorsement|exclusion),coverage,name,value}`, `rating_inputs{payroll_by_class[],experience_mod{},class_rates[],vehicle_count,gross_sales}`, `statements{}`, `operations_described[]` |
| `broker_email` | `subject`, `body` (untrusted free text) |
| `submission_checklist` | `items[]{item,status,note}` |
| `ops_supplement` | `operations[]{description,location_id}`, `narrative`, `peak_shipment_value` |
| `external_records` | `observations[]{source_name,subject,entity_name,address,state,record_date,record_identifier,services[]}` |

## Canonical model

`CanonicalCase` aggregates: documents, evidence store, entities, locations,
vehicles, drivers, claims, WC payroll rows, rating factors, quote terms,
exposures, external observations, checklist items, narratives, version links.

For unfamiliar or unstructured JSON, optional LLM-assisted extraction receives
only a privacy-scrubbed schema view (field names and scalar types; not source
values, account names, or document IDs). It may propose exposure types and
exact RFC 6901 JSON pointers or array templates. A deterministic validator
resolves every expanded pointer against the original input and derives each
stored value from that source (the model never supplies a replacement value).
Financial and count units are restricted to supported units; annual claim
counts require a source-backed period. An independent LLM judge reviews the
scrubbed schema and proposed mappings before they enter the canonical case.
Invalid paths, ambiguous units, judge disagreement, or unavailable extraction
are surfaced as incomplete; they are not silently treated as clean.

Each review writes an immutable, versioned exposure-model JSON snapshot under
`SPECTRA_DATA_DIR/extracted_models/<submission-hash>/<run-hash>.json`. It
contains typed exposures and their evidence references, not the full source
documents. Raw input remains the source of truth.

Every record field has an `EvidenceItem` with: `evidence_id`
(`EV-<doc>-<seq>`), `document_id`, `field_path`, raw + normalized values,
unit, period, snippet, and `SourceLocation` (`available=false` when the
input supplied no provenance). Version-difference checks may also cite the
locally computed document-content hash as a reproducible fingerprint; it is
explicitly marked as a snapshot, not as a source-page citation.

## Finding contract

See `app/schemas/findings.py`. Key properties: stable `finding_id`,
`taxonomy_id`, `rule_id`/`rule_version`, classification
(MANIFESTED_CATEGORY / NON_MANIFESTED_DISCOVERY / EVIDENCE_GAP /
SYSTEM_CHECK_FAILURE), conclusion state, severity, materiality (band +
rationale; impact estimates only with approved rating inputs), confidence
(`value` is null unless empirically calibrated), evidence refs, comparison
block (values, delta, formula, tolerance + source, entity/period
alignment), deduplication key, related findings, and append-only
disposition history.

`MANIFESTED_CATEGORY` is applied **only by the evaluation harness** when
labeling matches; runtime findings are never pre-labeled with answer-key
classifications.
