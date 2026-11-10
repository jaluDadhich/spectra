# Spectra Review Report — SPX-A03-2026-1042

- Run ID: `RUN-752DEDB2297F`
- Overall status: **REVIEW**
- Partial run: no
- Created: 2026-10-28T19:20:56.776311+00:00  Completed: 2026-10-28T19:20:56.856257+00:00
- Configuration: profile=commercial_package_default@1.0.0, ruleset=default@1.0.0, prompts=base_system@1.0.0;semantic_review@1.0.0;explain_finding@1.0.0, model=disabled

## Check summary

- COMPLETED: 22
- SKIPPED_NOT_APPLICABLE: 2

## Findings (17)

### [HIGH] Declared power units (12) vs active fleet schedule units (2)

- Finding ID: `F-5E80E09FCA8B` | Taxonomy: P3-FLEET-COUNT | Rule: P3-FLEET-COUNT-001 v1.0.0
- Status: OPEN | Conclusion: CONFIRMED_DATA_CONFLICT | Materiality: HIGH
- Materiality rationale: Vehicle count is a pricing-sensitive rating input
- Comparison: values=[12.0, 2] delta=10.0 (500.0%) formula=declared 12.0 - unique active units 2 tolerance=0.0 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=PARTIAL

**Why it matters:** Quote may be rated on a different unit count than the eligible fleet

**Recommended action:** Reconcile declared power units against the fleet schedule; confirm inclusion criteria and reporting dates before adjusting rating inputs

**Evidence:**

- `EV-A-QUOTE-0015` (A-QUOTE): raw=12 normalized=12.0 unit=count period=None [json_path=rating_inputs.vehicle_count]
- `EV-A-FLEET-0002` (A-FLEET): raw='active' normalized='active' unit=None period=None [json_path=vehicles[0].status]
- `EV-A-FLEET-0001` (A-FLEET): raw='1FTYR10U33PA12345' normalized='1FTYR10U33PA12345' unit=None period=None [json_path=vehicles[0].vin]
- `EV-A-FLEET-0009` (A-FLEET): raw='active' normalized='active' unit=None period=None [json_path=vehicles[1].status]
- `EV-A-FLEET-0008` (A-FLEET): raw='1GBJC34R82F11223' normalized='1GBJC34R82F11223' unit=None period=None [json_path=vehicles[1].vin]

### [HIGH] WC payroll differs between acord_130 and wc_rating_workbook

- Finding ID: `F-6FDDD3E098C5` | Taxonomy: P3-WC-PAYROLL | Rule: P3-WC-PAYROLL-001 v1.0.0
- Status: OPEN | Conclusion: CONFIRMED_DATA_CONFLICT | Materiality: HIGH
- Materiality rationale: Payroll is the WC exposure base
- Uncertainty: Periods/inclusion criteria assumed comparable; confirm before treating as final
- Comparison: values=[2000000.0, 2240000.0] delta=-240000.0 (-10.71%) formula=acord_130 total 2000000.0 - wc_rating_workbook total 2240000.0 tolerance=0.0 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=PARTIAL

**Why it matters:** Quote may use payroll inconsistent with the application

**Recommended action:** Reconcile payroll by class and location across the application, workbook, and quote; verify periods align

**Evidence:**

- `EV-A-ACORD130-0003` (A-ACORD130): raw=1200000 normalized=1200000.0 unit=USD period=None [json_path=class_rows[0].payroll]
- `EV-A-ACORD130-0008` (A-ACORD130): raw=800000 normalized=800000.0 unit=USD period=None [json_path=class_rows[1].payroll]
- `EV-A-WCWB-0003` (A-WCWB): raw=1350000 normalized=1350000.0 unit=USD period=None [sheet=Payroll by Class, row=5, cell=D5]
- `EV-A-WCWB-0009` (A-WCWB): raw=800000 normalized=800000.0 unit=USD period=None [sheet=Payroll by Class, row=6, cell=D6]
- `EV-A-WCWB-0015` (A-WCWB): raw=90000 normalized=90000.0 unit=USD period=None [json_path=payroll_rows[2].payroll]

### [HIGH] WC payroll differs between quote and wc_rating_workbook

- Finding ID: `F-DABC9F3D241F` | Taxonomy: P3-WC-PAYROLL | Rule: P3-WC-PAYROLL-001 v1.0.0
- Status: OPEN | Conclusion: CONFIRMED_DATA_CONFLICT | Materiality: HIGH
- Materiality rationale: Payroll is the WC exposure base
- Uncertainty: Periods/inclusion criteria assumed comparable; confirm before treating as final
- Comparison: values=[2000000.0, 2240000.0] delta=-240000.0 (-10.71%) formula=quote total 2000000.0 - wc_rating_workbook total 2240000.0 tolerance=0.0 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=PARTIAL

**Why it matters:** Quote may use payroll inconsistent with the application

**Recommended action:** Reconcile payroll by class and location across the application, workbook, and quote; verify periods align

**Evidence:**

- `EV-A-QUOTE-0008` (A-QUOTE): raw=1200000 normalized=1200000.0 unit=USD period=None [json_path=rating_inputs.payroll_by_class[0].payroll]
- `EV-A-QUOTE-0011` (A-QUOTE): raw=800000 normalized=800000.0 unit=USD period=None [json_path=rating_inputs.payroll_by_class[1].payroll]
- `EV-A-WCWB-0003` (A-WCWB): raw=1350000 normalized=1350000.0 unit=USD period=None [sheet=Payroll by Class, row=5, cell=D5]
- `EV-A-WCWB-0009` (A-WCWB): raw=800000 normalized=800000.0 unit=USD period=None [sheet=Payroll by Class, row=6, cell=D6]
- `EV-A-WCWB-0015` (A-WCWB): raw=90000 normalized=90000.0 unit=USD period=None [json_path=payroll_rows[2].payroll]

### [HIGH] WC workbook declared total payroll does not equal sum of class/location rows

- Finding ID: `F-0B2BCF82820A` | Taxonomy: P3-WC-PAYROLL | Rule: P3-WC-PAYROLL-001 v1.0.0
- Status: OPEN | Conclusion: CONFIRMED_DATA_CONFLICT | Materiality: HIGH
- Materiality rationale: Payroll is the WC exposure base
- Comparison: values=[2390000.0, 2240000.0] delta=150000.0 (6.7%) formula=declared 2390000.0 - sum(rows) 2240000.0 tolerance=0.0 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=MATCHED

**Why it matters:** Rating input payroll is internally inconsistent

**Recommended action:** Obtain a corrected workbook; do not alter rating inputs in Spectra

**Evidence:**

- `EV-A-WCWB-0022` (A-WCWB): raw=2390000 normalized=2390000.0 unit=USD period=None [json_path=declared_totals.total_payroll]
- `EV-A-WCWB-0003` (A-WCWB): raw=1350000 normalized=1350000.0 unit=USD period=None [sheet=Payroll by Class, row=5, cell=D5]
- `EV-A-WCWB-0009` (A-WCWB): raw=800000 normalized=800000.0 unit=USD period=None [sheet=Payroll by Class, row=6, cell=D6]
- `EV-A-WCWB-0015` (A-WCWB): raw=90000 normalized=90000.0 unit=USD period=None [json_path=payroll_rows[2].payroll]

### [HIGH] Evidence-backed cargo_value (250,000) exceeds quoted cargo limit (100,000)

- Finding ID: `F-CCC5828B00E8` | Taxonomy: P11 | Rule: P11-EXPOSURE-LIMIT-001 v1.0.0
- Status: OPEN | Conclusion: CONFIRMED_DATA_CONFLICT | Materiality: HIGH
- Materiality rationale: Potential under-insurance of an evidence-backed peak exposure
- Uncertainty: Data inconsistency confirmed; coverage outcome requires specialist interpretation of policy wording
- Comparison: values=[250000.0, 100000.0] delta=150000.0 (150.0%) formula=exposure 250000.0 - limit 100000.0 tolerance=0.0 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=PARTIAL

**Why it matters:** Peak exposure exceeds the quoted limit by 150,000 (150.0%).

**Recommended action:** Refer to a coverage specialist; confirm peak values with the insured and consider limit adequacy. No legal coverage conclusion is made.

**Evidence:**

- `EV-A-OPS-0002` (A-OPS): raw=250000 normalized=250000.0 unit=USD period=None [json_path=peak_shipment_value]
- `EV-A-QUOTE-0005` (A-QUOTE): raw=100000 normalized=100000.0 unit=USD period=None [json_path=terms[4].value]

### [HIGH] Experience modification factor differs across documents

- Finding ID: `F-111CA1DA7007` | Taxonomy: P6 | Rule: P6-EMOD-001 v1.0.0
- Status: OPEN | Conclusion: POSSIBLE_MISMATCH | Materiality: HIGH
- Materiality rationale: Experience mod directly multiplies WC premium
- Uncertainty: Approved current experience-mod source unavailable; which value is correct cannot be determined from the submission alone
- Comparison: values=[0.9, 1.02, 1.02] delta=None (None%) formula=None tolerance=0.0001 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=MATCHED

**Why it matters:** Quote may apply an incorrect experience modification: A-ACORD130: 0.9 (eff 2026-01-01); A-QUOTE: 1.02 (eff 2026-01-01); A-WCWB: 1.02 (eff 2026-01-01)

**Recommended action:** Obtain the current bureau experience-mod worksheet for the proposed effective date and validate the factor; do not select a factor based on recency alone

**Evidence:**

- `EV-A-ACORD130-0011` (A-ACORD130): raw=0.9 normalized=0.9 unit=None period=2026-01-01 [json_path=experience_mod.factor]
- `EV-A-QUOTE-0012` (A-QUOTE): raw=1.02 normalized=1.02 unit=None period=2026-01-01 [json_path=rating_inputs.experience_mod.factor]
- `EV-A-WCWB-0019` (A-WCWB): raw=1.02 normalized=1.02 unit=None period=2026-01-01 [json_path=current_experience_mod.factor]

### [HIGH] No current driver roster / MVR review evidence in submission

- Finding ID: `F-2A5A60341914` | Taxonomy: P1 | Rule: P1-DRIVER-ROSTER-001 v1.0.0
- Status: OPEN | Conclusion: EVIDENCE_GAP | Materiality: HIGH
- Materiality rationale: Driver qualification cannot be reviewed without a current roster
- Uncertainty: Missing evidence is not proof that any driver is unqualified

**Why it matters:** Driver eligibility, MVR review status, and heavy-vehicle qualification cannot be verified; 'driver_roster' marked missing; 'quote_statement:driver_roster_attached' marked False; fleet schedule references assigned drivers

**Recommended action:** Request the current driver roster and MVR review evidence; do not infer driver disqualification from absence

**Evidence:**

- `EV-A-CHECKLIST-0003` (A-CHECKLIST): raw='missing' normalized='missing' unit=None period=None [json_path=items[2].status]
- `EV-A-QUOTE-0017` (A-QUOTE): raw=False normalized=False unit=None period=None [json_path=statements.driver_roster_attached]
- `EV-A-FLEET-0006` (A-FLEET): raw='D1' normalized='D1' unit=None period=None [json_path=vehicles[0].assigned_driver_id]
- `EV-A-FLEET-0013` (A-FLEET): raw='D2' normalized='D2' unit=None period=None [json_path=vehicles[1].assigned_driver_id]

### [HIGH] 1 heavy vehicle(s) (GVWR >= 26001) but no driver roster/credentials supplied

- Finding ID: `F-CD3FB10E8400` | Taxonomy: N8 | Rule: N8-DRIVER-QUAL-001 v1.0.0
- Status: OPEN | Conclusion: EVIDENCE_GAP | Materiality: MEDIUM
- Materiality rationale: Qualification of heavy-vehicle drivers cannot be reviewed
- Uncertainty: Absent roster/credentials is an evidence gap, not a confirmed qualification mismatch

**Why it matters:** Heavy-vehicle driver qualification cannot be verified

**Recommended action:** Request driver roster with license class/CDL endorsements for assigned drivers of heavy units

**Evidence:**

- `EV-A-FLEET-0003` (A-FLEET): raw=33000 normalized=33000.0 unit=None period=None [json_path=vehicles[0].gvwr]
- `EV-A-FLEET-0006` (A-FLEET): raw='D1' normalized='D1' unit=None period=None [json_path=vehicles[0].assigned_driver_id]

### [MEDIUM] Unit U-2: garaging state code OR conflicts with garaging address state CA

- Finding ID: `F-33C9D721E8DE` | Taxonomy: N7 | Rule: N7-GARAGING-001 v1.0.0
- Status: OPEN | Conclusion: CONFIRMED_DATA_CONFLICT | Materiality: MEDIUM
- Materiality rationale: Garaging state drives territory/rating
- Comparison: values=['OR', 'CA'] delta=None (None%) formula=None tolerance=None (ruleset configuration)
- Alignment: entity=MATCHED period=MATCHED

**Why it matters:** Territory/rating may be wrong for this unit; neither field is overwritten automatically

**Recommended action:** Confirm the actual garaging address and state for the unit

**Evidence:**

- `EV-A-FLEET-0011` (A-FLEET): raw='OR' normalized='OR' unit=None period=None [json_path=vehicles[1].garaging_state]
- `EV-A-FLEET-0012` (A-FLEET): raw='250 Depot Road, Sacramento, CA 95814' normalized='250 Depot Road, Sacramento, CA 95814' unit=None period=None [json_path=vehicles[1].garaging_address]
- `EV-A-FLEET-0008` (A-FLEET): raw='1GBJC34R82F11223' normalized='1GBJC34R82F11223' unit=None period=None [json_path=vehicles[1].vin]

### [MEDIUM] ACORD 130 schedule omits workbook state(s): AZ

- Finding ID: `F-CF6CD33F8718` | Taxonomy: N3 | Rule: N3-WC-STATE-001 v1.0.0
- Status: OPEN | Conclusion: CONFIRMED_DATA_CONFLICT | Materiality: MEDIUM
- Materiality rationale: Incomplete state schedule can omit exposure from review
- Comparison: values=[['CA'], ['AZ', 'CA']] delta=None (None%) formula=None tolerance=None (ruleset configuration)
- Alignment: entity=MATCHED period=PARTIAL

**Why it matters:** Workbook allocates payroll to states not shown on the application

**Recommended action:** Confirm where work is performed and correct the ACORD 130 schedule

**Evidence:**

- `EV-A-WCWB-0014` (A-WCWB): raw='AZ' normalized='AZ' unit=None period=None [json_path=payroll_rows[2].state]
- `EV-A-WCWB-0013` (A-WCWB): raw='8810' normalized='8810' unit=None period=None [json_path=payroll_rows[2].class_code]
- `EV-A-WCWB-0015` (A-WCWB): raw=90000 normalized=90000.0 unit=USD period=None [json_path=payroll_rows[2].payroll]

### [MEDIUM] External/public context describes operations not declared: hazardous materials hauling

- Finding ID: `F-A9DE112342DB` | Taxonomy: P7 | Rule: P7-OPERATIONS-001 v1.0.0
- Status: OPEN | Conclusion: POSSIBLE_MISMATCH | Materiality: MEDIUM
- Materiality rationale: Undeclared operations may be unpriced or excluded
- Uncertainty: External record matched on name only (Normalized legal names match exactly; name match alone does not establish identity); company-wide public context does not prove exposure at a specific insured premise

**Why it matters:** Operations described publicly are not reflected in the application or quote operations

**Recommended action:** Ask the broker/insured whether these services are performed, at which premises, and whether the quote covers them

**Evidence:**

- `EV-A-EXT-0001` (A-EXT): raw={'record_id': 'PUB-118', 'source_name': 'OSHA', 'subject': 'inspection', 'entity_name': 'Sierra Pacific Logistics LLC', 'address': '100 Warehouse Way', 'city': 'Fresno', 'state': 'CA', 'zip': '93721', 'record_date': '2026-02-14', 'retrieved_at': '2026-10-18', 'record_identifier': 'osha-inspection-118', 'services': ['warehousing', 'hazardous materials hauling']} normalized={'record_id': 'PUB-118', 'source_name': 'OSHA', 'subject': 'inspection', 'entity_name': 'Sierra Pacific Logistics LLC', 'address': '100 Warehouse Way', 'city': 'Fresno', 'state': 'CA', 'zip': '93721', 'record_date': '2026-02-14', 'retrieved_at': '2026-10-18', 'record_identifier': 'osha-inspection-118', 'services': ['warehousing', 'hazardous materials hauling']} unit=None period=None [json_path=observations[0]]
- `EV-A-OPS-0001` (A-OPS): raw={'description': 'warehousing and local delivery', 'location_id': 'LOC-1'} normalized={'description': 'warehousing and local delivery', 'location_id': 'LOC-1'} unit=None period=None [json_path=operations[0]]
- `EV-A-QUOTE-0019` (A-QUOTE): raw='warehousing' normalized='warehousing' unit=None period=None [json_path=operations_described[0]]
- `EV-A-QUOTE-0020` (A-QUOTE): raw='local delivery' normalized='local delivery' unit=None period=None [json_path=operations_described[1]]
- `EV-A-ACORD125-0014` (A-ACORD125): raw='Warehousing and local delivery operations from two California locations.' normalized='Warehousing and local delivery operations from two California locations.' unit=None period=None [json_path=operations_description]
- `EV-A-EMAIL-0001` (A-EMAIL): raw='Please find the renewal submission for Sierra Pacific Logistics. Operations are warehousing and local delivery from two California locations.' normalized='Please find the renewal submission for Sierra Pacific Logistics. Operations are warehousing and local delivery from two California locations.' unit=None period=None [json_path=body]

### [MEDIUM] GL gross sales not reconciled to sales-rated exposure rows

- Finding ID: `F-2AD4DA7B39A8` | Taxonomy: N6 | Rule: N6-GL-BASES-001 v1.0.0
- Status: OPEN | Conclusion: POSSIBLE_MISMATCH | Materiality: MEDIUM
- Materiality rationale: Sales are the GL rating base
- Uncertainty: Unexplained remainder is not assumed to be missing sales: allocation, exclusions, or alternate bases may explain it
- Comparison: values=[8000000.0, 7200000.0] delta=800000.0 (10.0%) formula=declared sales 8000000.0 - sum(sales-rated rows) 7200000.0 tolerance=0.0 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=PARTIAL

**Why it matters:** GL premium base may be incomplete or double-counted

**Recommended action:** Request the sales allocation supporting the rated exposures

**Evidence:**

- `EV-A-ACORD125-0011` (A-ACORD125): raw=8000000 normalized=8000000.0 unit=USD period=annual [json_path=gross_sales]
- `EV-A-ACORD126-0001` (A-ACORD126): raw=4200000 normalized=4200000.0 unit=USD period=None [json_path=sales_rows[0].amount]
- `EV-A-ACORD126-0002` (A-ACORD126): raw=3000000 normalized=3000000.0 unit=USD period=None [json_path=sales_rows[1].amount]

### [MEDIUM] WC class 8810 (CA) rates disagree across documents

- Finding ID: `F-72F75EE79C2B` | Taxonomy: N15 | Rule: N15-CLASS-RATE-001 v1.0.0
- Status: OPEN | Conclusion: POSSIBLE_MISMATCH | Materiality: MEDIUM
- Materiality rationale: Class rates are pricing inputs
- Uncertainty: No approved filed-rate source supplied; a workbook or quote rate is not authenticated rate support
- Comparison: values=[1.75, 1.25] delta=None (None%) formula=None tolerance=0.0001 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=PARTIAL

**Why it matters:** Premium may be computed with an unvalidated rate: A-QUOTE: 1.75 (source quote); A-WCWB: 1.25 (source workbook)

**Recommended action:** Validate the rate against approved carrier/bureau rate pages for the state and effective date; do not assume either table is authoritative

**Evidence:**

- `EV-A-QUOTE-0013` (A-QUOTE): raw=1.75 normalized=1.75 unit=None period=None [json_path=rating_inputs.class_rates[0].rate]
- `EV-A-WCWB-0020` (A-WCWB): raw=1.25 normalized=1.25 unit=None period=None [json_path=rate_tables[0].rate]

### [MEDIUM] Full-time employee counts differ: acord_125=25 vs wc_rating_workbook=20

- Finding ID: `F-31E888CB6FC2` | Taxonomy: N4 | Rule: N4-EMP-COUNT-001 v1.0.0
- Status: OPEN | Conclusion: CONFIRMED_DATA_CONFLICT | Materiality: UNKNOWN
- Materiality rationale: n/a
- Uncertainty: Totals compared on the same classification basis; payroll reconciliation does not prove count reconciliation
- Comparison: values=[25.0, 20.0] delta=5.0 (25.0%) formula=acord_125 full-time 25.0 - wc_rating_workbook full-time 20.0 tolerance=0.0 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=PARTIAL

**Why it matters:** Headcount-dependent checks and premium bases may be inconsistent

**Recommended action:** Confirm full/part-time counts by location and class

**Evidence:**

- `EV-A-ACORD125-0012` (A-ACORD125): raw=25 normalized=25.0 unit=None period=None [json_path=employee_counts.full_time]
- `EV-A-WCWB-0005` (A-WCWB): raw=12 normalized=12.0 unit=None period=None [json_path=payroll_rows[0].full_time]
- `EV-A-WCWB-0011` (A-WCWB): raw=8 normalized=8.0 unit=None period=None [json_path=payroll_rows[1].full_time]

### [MEDIUM] Required document missing: driver_roster

- Finding ID: `F-93EDFD901C3C` | Taxonomy: DOC | Rule: DOC-INVENTORY-001 v1.0.0
- Status: OPEN | Conclusion: EVIDENCE_GAP | Materiality: MEDIUM
- Materiality rationale: Current driver roster / MVR review evidence
- Uncertainty: Document absent from submission; absence is an evidence gap, not proof of an adverse fact

**Why it matters:** Review cannot confirm the checks that depend on this document

**Recommended action:** Request the driver_roster from the broker or mark the requirement not applicable with rationale

**Evidence:**

- (no field-level evidence; configuration/profile-based finding)

### [MEDIUM] Full-time employee counts differ: acord_125=25 vs acord_130=20

- Finding ID: `F-9F31DFDBE0C9` | Taxonomy: N4 | Rule: N4-EMP-COUNT-001 v1.0.0
- Status: OPEN | Conclusion: CONFIRMED_DATA_CONFLICT | Materiality: UNKNOWN
- Materiality rationale: n/a
- Uncertainty: Totals compared on the same classification basis; payroll reconciliation does not prove count reconciliation
- Comparison: values=[25.0, 20.0] delta=5.0 (25.0%) formula=acord_125 full-time 25.0 - acord_130 full-time 20.0 tolerance=0.0 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=PARTIAL

**Why it matters:** Headcount-dependent checks and premium bases may be inconsistent

**Recommended action:** Confirm full/part-time counts by location and class

**Evidence:**

- `EV-A-ACORD125-0012` (A-ACORD125): raw=25 normalized=25.0 unit=None period=None [json_path=employee_counts.full_time]
- `EV-A-ACORD130-0004` (A-ACORD130): raw=12 normalized=12.0 unit=None period=None [json_path=class_rows[0].full_time]
- `EV-A-ACORD130-0009` (A-ACORD130): raw=8 normalized=8.0 unit=None period=None [json_path=class_rows[1].full_time]

### [MEDIUM] loss_summary reports 4 claims for 2025; loss detail contains 2

- Finding ID: `F-7480831A8C51` | Taxonomy: N5 | Rule: N5-ANNUAL-COUNTS-001 v1.0.0
- Status: OPEN | Conclusion: CONFIRMED_DATA_CONFLICT | Materiality: UNKNOWN
- Materiality rationale: n/a
- Uncertainty: Policy-year vs calendar-year basis assumed; confirm if disputed
- Comparison: values=[4.0, 2] delta=2.0 (100.0%) formula=loss_summary 2025 count 4 - loss detail 2 tolerance=0.0 (default ruleset 1.0.0)
- Alignment: entity=MATCHED period=MATCHED

**Why it matters:** Annual claim frequency used in underwriting may be misstated

**Recommended action:** Reconcile the annual claim counts with the loss detail listing

**Evidence:**

- `EV-A-LOSSRUN-0015` (A-LOSSRUN): raw=4 normalized=4.0 unit=None period=2025 [json_path=summary.annual_counts[0].count]

---
LLM usage: calls=0 input_tokens=0 output_tokens=0 est_cost_usd=None retries=0 errors=0

Spectra is decision support. It does not bind, decline, change premiums, or make legal coverage determinations.