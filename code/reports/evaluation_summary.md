# Spectra evaluation summary — synthetic accounts A01/A02/A03

Measured 2026-10-28, deterministic-only mode (LLM disabled), ruleset
`default@1.0.0`, profile `commercial_package_default@1.0.0`.
Per-account detail: `evaluation_a01.json`, `evaluation_a02.json`,
`evaluation_a03.json`. Fixture intent: `samples/README.md`.

> These fixtures were authored to exercise the taxonomy, so perfect scores
> demonstrate rule/fixture agreement — not real-world performance. See
> docs/limitations.md.

| Account | TP | FP | FN | Precision | Recall | F1 | Manifested recall | Excluded violations | Dup-alert rate | Citation accuracy |
|---|---|---|---|---|---|---|---|---|---|---|
| A01 | 11 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 | none | 0.0 | 1.000 |
| A02 | 11 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 | none | 0.0 | 0.933 |
| A03 | 12 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 | none | 0.0 | 0.941 |

Clean control account (`samples/clean`): **PASS**, 0 findings — no false
positives on a consistent submission.

Citation accuracy < 1.0 reflects profile-based findings (e.g. missing
required document) that legitimately carry no field-level evidence.

## Deduplication checks

- A02: P5 detected (duplicate claim `CLM-2001` with conflicting financials);
  N5 correctly NOT raised — the annual-count evidence was not independent
  of the P5 fact pattern (excluded-label check passes).
- A03: N14 correctly NOT raised — the external record's premises identity
  resolved to a scheduled location, so the identity/scope issue is carried
  by P7 only (excluded-label check passes).

## Uncertainty behavior spot checks

- P6 (A01/A03): reported as POSSIBLE_MISMATCH with "requires validation" —
  no authoritative current-factor source supplied; no winner chosen.
- N8 (A01): possible license-class mismatch; (A03): evidence gap only —
  absence of a roster is never reported as disqualification.
- N14 (A01/A02): name-only external record match → POSSIBLE_MISMATCH /
  needs human review, never a confirmed conflict.
