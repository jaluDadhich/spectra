# Rule catalog

Rules are configuration-driven: metadata in `rulesets/default.json`
(versioned), logic in `app/rules/handlers.py`. Startup validation
(`spectra rules validate`) fails if config and handlers disagree.
Tolerances/severities below are **defaults** — carrier-specific values must
be supplied by the carrier before production use.

## Issue taxonomy rules

The exclusive issue taxonomy and LLM adjudication policy live in
`rulesets/unified_issue_register.json`. Every runtime finding must map to
exactly one U01-U14 category; unmatched deterministic or model-suggested
issues are dropped. Infrastructure/data-quality checks remain visible as
checks but are not emitted as underwriting findings. The LangGraph
`scope_auditor` maps candidate findings to the register, validates cited
evidence, and searches the supplied evidence for missed in-scope issues. If
the call, schema, or evidence validation fails, review falls back to
deterministic checks and keeps only exact rule-to-U-category mappings.
Each scope-judge batch includes all evidence cited by its candidates, up to
`SPECTRA_SCOPE_EVIDENCE_PAYLOAD_CHARS` (42,000 by default). Each LLM request
enforces an estimated 25,000-token ceiling across prompt and completion. If
the cited evidence for even one candidate cannot fit, the audit is marked
unavailable and the deterministic scoped path is used.
The final judge greedily packs at most 25 findings per call based on the same
token estimator used by the gateway, reserving completion capacity for the
structured decisions. A finding and all of its cited evidence stay together;
an individual candidate that cannot fit triggers deterministic fallback.

The catalog describes issue patterns, not required account-specific values.
It is based on the supplied consolidated register, which explicitly cautions
that its example figures are synthetic and are not independently validated.
This catalog is specific to the current commercial insurance submission
review scope; it does not claim to cover arbitrary non-insurance review work.

| Unified category | Rule coverage |
|---|---|
| U01 | Location and property-schedule conflicts (P2-LOCATION-001) |
| U02 | Unexplained differences between document versions (P4-VERSION-001) |
| U03 | Workers' compensation payroll conflicts (P3-WC-PAYROLL-001) |
| U04 | Experience-modification factor conflicts or unverified authority (P6-EMOD-001) |
| U05 | Workers' compensation state coverage conflicts (N3-WC-STATE-001) |
| U06 | Employee-count conflicts (N4-EMP-COUNT-001) |
| U07 | Class-rate conflicts without filed-rate support (N15-CLASS-RATE-001) |
| U08 | Claim detail, claim-count, and loss-history inconsistencies (P5-CLAIMS-001, N5-ANNUAL-COUNTS-001) |
| U09 | General-liability sales/area rating-basis conflicts (N6-GL-BASES-001) |
| U10 | Declared fleet count vs. active scheduled units (P3-FLEET-COUNT-001) |
| U11 | Garaging-state vs. garaging-address conflicts (N7-GARAGING-001) |
| U12 | Driver roster, qualification, and MVR evidence issues (P1-DRIVER-ROSTER-001, N8-DRIVER-QUAL-001) |
| U13 | Operations declarations and external-record matching (P7-OPERATIONS-001, N14-EXTERNAL-001) |
| U14 | Evidence-backed exposure vs. quoted limit conflicts (P11-EXPOSURE-LIMIT-001) |

For U12, a roster licence class or MVR review date alone does not establish
driver qualification, disqualification, an MVR result, or staleness. When the
applicable jurisdiction/carrier criteria or supporting credential results are
missing, report an unverified evidence gap only; do not imply a violation.

Document-inventory and generic data-quality checks are infrastructure
checks, not a fifteenth issue category; they do not appear as runtime issue
findings.

## Data-quality rules

| Rule ID | What it detects |
|---|---|
| DQ-DUP-ID-001 | Duplicate record IDs within a document |
| DQ-ARITH-001 | Internal totals (e.g. TIV ≠ building + contents) |
| DQ-DATES-001 | Invalid date ordering (effective period, loss vs valuation) |
| DQ-STALE-001 | Documents older than profile freshness limits |

## Writing a new rule

1. Add the handler in `app/rules/handlers.py` (pure function of
   `RuleContext` → `list[FindingDraft]`; cite evidence via
   `record.field_evidence`).
2. Register it in `all_handlers()`.
3. Add a versioned entry to `rulesets/default.json` (or a new ruleset) with
   applicability conditions and tolerances.
4. `python -m app.cli.main rules validate`
5. Add unit tests in `tests/unit/test_rules.py` — one per behavior branch.

Rollback: keep prior ruleset JSON files (e.g. `rulesets/default-1.0.0.json`)
and select with `--ruleset`.
