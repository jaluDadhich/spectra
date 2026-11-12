# SPECTRA

**Submission Pre-Bind Evidence Check and Truth Review Agent** — decision support
for commercial insurance underwriting. Spectra reviews pre-normalized JSON
submission documents before binding and produces evidence-backed, traceable,
deduplicated, uncertainty-calibrated findings.

> Spectra never binds, declines, changes premiums, or issues legal coverage
> determinations. Human underwriters own final disposition.

## What it does

- Ingests per-document JSON (ACORD 125/126/130/140, SOV, WC rating workbook,
  loss runs, fleet schedule, driver roster, draft quote, broker narrative,
  checklist, ops supplement, structured external records).
- Inventories documents against a configurable required-document profile.
- Normalizes into a canonical, evidence-linked data model (raw + normalized
  values, units, periods, source provenance — never fabricated).
- Runs deterministic checks and reports underwriting issues exclusively in
  the 14-category U01–U14 taxonomy from the unified issue register.
- Parses cell-level workbooks and combined ACORD JSON; optional LLM extraction
  of unfamiliar exposure schemas is citation-validated and independently
  judged before being added to the canonical exposure model.
- Persists versioned, evidence-linked exposure-model snapshots under
  `SPECTRA_DATA_DIR/extracted_models/`.
- Runs a LangGraph workflow with intake, conditional WC/GL/Auto/Property agents,
  a critic feedback pass, SQLite checkpoints, and per-agent traces.
- Uses supplied `external_records` locally first; optional live OSHA/state/news
  search is opt-in and cached.
- Deduplicates findings, ranks by explainable severity/materiality, computes a
  case status (PASS / REVIEW / HOLD / ERROR-INCOMPLETE), and supports reviewer
  dispositions with a full audit trail.
- Scores each processed submission's data quality (0–100, grade A–F) with an
  itemized, deterministic penalty breakdown — recomputed identically for
  historical runs.
- Dashboard account picker: start a run per account folder (prior runs for
  the same account are overridden, audit trail preserved) with a live
  per-stage progress bar (`GET /api/v1/runs/{run_id}/progress`).
- Exports JSON / CSV / human-readable Markdown reports.

## Quick start

```powershell
pip install -r requirements.txt        # or: pip install -e .
python samples/generate_samples.py     # synthetic fixtures (already generated)

# CLI
python -m app.cli.main review --submission-id SPX-A03-2026-1042 `
    --input-dir samples\a03 --line-of-business package `
    --effective-date 2026-11-15 --review-date 2026-10-28 --deterministic-only

# API + UI
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8765
# UI:  http://127.0.0.1:8765/         API docs: http://127.0.0.1:8765/docs

# Tests (no API key needed)
python -m pytest
```

## CLI commands

| Command | Purpose |
|---|---|
| `spectra validate --submission-id … --input-dir …` | Validate input JSON only |
| `spectra review …` | Full review (exit 3 on ERROR/INCOMPLETE) |
| `spectra export --run-id … --format json\|csv\|markdown` | Export a run |
| `spectra evaluate --predictions-run-id … --ground-truth …` | Score vs labels |
| `spectra rules validate [--ruleset default]` | Validate ruleset config |

## Configuration

Copy `.env.example` to `.env` in the `code/` directory and set
`OPENAI_API_KEY=your_key`. Set `SPECTRA_LLM_ENABLED=true` to run the
whole-submission GPT review first; the deterministic rules run when GPT is
disabled, unavailable, or its review response cannot be validated.
`OPENAI_FAST_MODEL` defaults to `gpt-6-luna` for short explanation tasks and
`OPENAI_REASONING_MODEL` defaults to `gpt-6.1-terra` for cross-document review.
Reasoning effort defaults to low for fast tasks and high for comparisons.
All are configurable, with `OPENAI_MODEL` retained as a legacy fallback. Key
settings also include budgets
(`SPECTRA_LLM_BUDGET_USD_PER_CASE`, `SPECTRA_MAX_LLM_CALLS_PER_CASE`),
`DATABASE_URL`, `SPECTRA_API_TOKEN` (API auth hook). See
[docs/configuration.md](docs/configuration.md) and
[docs/model-cost-guide.md](docs/model-cost-guide.md).

## Repository layout

```
app/
  config/  schemas/  adapters/  normalization/  catalog/  rules/
  reconciliation logic lives in rules/handlers.py (cross-document rules)
  entity_resolution/  llm/  prompts/  agents/  external_sources/
  findings/  evidence/  storage/  evaluation/  api/  cli/  ui/
rulesets/default.json          # versioned rule registry (tolerances, severity)
profiles/commercial_package_default.json
samples/                       # SYNTHETIC fixtures + generator
evaluation/ground_truth/       # evaluation labels — never read by runtime
tests/                         # unit, integration, e2e, security
docs/                          # architecture, schemas, rules, runbook, …
reports/                       # sample exported review report
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — modules, workflow stages, diagram
- [docs/schemas.md](docs/schemas.md) — input JSON shapes and canonical model
- [docs/rules.md](docs/rules.md) — U01–U14 issue categories and rule coverage
- [docs/configuration.md](docs/configuration.md) — environment, profiles, rulesets
- [docs/runbook.md](docs/runbook.md) — operating instructions
- [docs/model-cost-guide.md](docs/model-cost-guide.md) — LLM config, budgets
- [docs/limitations.md](docs/limitations.md) — limitations & production checklist

## Important guarantees

- Runtime detection never loads evaluation labels
  (`evaluation/ground_truth/` is read only by `spectra evaluate` and tests).
- Every finding cites existing evidence IDs; hallucinated citations are rejected.
- Missing evidence is reported as a gap — never as proof of an adverse fact.
- External verification adapters are disabled by default; an unavailable
  source marks the check INCOMPLETE, which can never become PASS.
