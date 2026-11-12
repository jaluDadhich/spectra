# Underwriting quality score

Spectra displays five deterministic underwriting-quality dimensions. Each dimension starts at 100 and loses points for distinct, in-scope issue families. Repeated findings within the same family are counted once.

| Dimension | Weight | Primary signals |
|---|---:|---|
| Evidence Consistency | 25% | Confirmed or possible conflicts, mismatches, data-quality issues, and material changes across sources |
| Pricing Input Reliability | 20% | Explicit stale, expired, outdated, or out-of-date rating inputs and supporting documents |
| Coverage Alignment | 20% | Conflicts between exposures and coverage terms, limits, sublimits, exclusions, or forms |
| Evidence Defensibility | 20% | Evidence gaps, unsupported declarations, weak citation locations, missing document evidence, and incomplete checks or review stages |
| Exposure Completeness | 15% | Undeclared, underdeclared, omitted, or unsupported operations and exposures |

## Formula

For each issue family, the base deduction is Critical = 24, High = 18, Medium = 12, Low = 5, Informational = 0. The deduction is multiplied by a conclusion factor: confirmed conflict = 1.00, possible mismatch = 0.70, evidence gap = 0.65, check incomplete = 0.60, and no discrepancy = 0.00. Findings are grouped by their issue family and assigned once to their primary dimension. Each dimension's deductions are capped at 80 points.

`Dimension score = max(0, 100 − capped deductions)`

`Overall = 0.25 × Evidence Consistency + 0.20 × Pricing Input Reliability + 0.20 × Coverage Alignment + 0.20 × Evidence Defensibility + 0.15 × Exposure Completeness`

The overall value is rounded to one decimal place. All weights, issue classification, grouping, severity values, conclusion factors, and arithmetic are deterministic. Finding severity may originate from the review process, but no LLM call is used to compute or adjust these scores. Pricing Input Reliability is reduced only when a staleness signal is explicit in a finding; Spectra does not assume an age limit for a document without an applicable rule or supplied review criterion.
