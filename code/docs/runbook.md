# Runbook

## Install

```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1   # optional
pip install -r requirements.txt                    # or: pip install -e .[dev]
copy .env.example .env                             # fill in as needed
```

## Run a review (CLI)

```powershell
python -m app.cli.main validate --submission-id SPX-X --input-dir path\to\json
python -m app.cli.main review --submission-id SPX-X --input-dir path\to\json `
    --line-of-business package --effective-date 2026-11-15 `
    --deterministic-only
python -m app.cli.main export --run-id RUN-… --format markdown --output report.md
```

Exit codes: `0` = review completed (PASS/REVIEW/HOLD are outcomes),
`1` = validation failures, `2` = usage error, `3` = run ended
ERROR/INCOMPLETE.

## Run the API + UI

```powershell
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

- UI: `http://127.0.0.1:8000/` (runs list → case screen → finding detail
  with evidence panel and disposition form)
- OpenAPI: `/docs`; liveness `/health`; readiness `/ready` (reports
  ruleset/profile health, never secrets).

### API quick reference

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/reviews` | Create review (submission_id + `documents[]` or `input_dir`) |
| `GET /api/v1/reviews` / `…/{run_id}` | List / get run |
| `GET …/findings` | Filter: `taxonomy`, `severity`, `status`, `limit`, `offset` |
| `GET …/findings/{id}` | Full evidence + comparison |
| `POST …/findings/{id}/disposition` | Reviewer action + rationale (required) |
| `POST …/rerun` | 501 — create a new review with updated inputs; history preserved |
| `GET …/export?format=json\|csv\|markdown` | Exports |

## Evaluation

```powershell
python -m app.cli.main review --submission-id SPX-A01-2026-0101 --input-dir samples\a01 --line-of-business package --deterministic-only
python -m app.cli.main evaluate --predictions-run-id RUN-… --ground-truth evaluation\ground_truth\a01.json
```

Metrics: precision/recall/F1, per-family recall, manifested recall,
excluded-label violations, duplicate-alert rate, evidence-citation accuracy,
token usage/cost. Ground truth is read only by this command and tests.

## Tests

```powershell
python -m pytest                 # full suite (no API key required)
python -m pytest tests\unit -q   # subsets
```

An opt-in live-model smoke test requires `SPECTRA_LLM_ENABLED=true` and a
real key; it is not part of CI.

## Operations

- **Rule failure** → that check is FAILED, case becomes ERROR/INCOMPLETE;
  other rules still run.
- **LLM outage** → semantic stage marked unavailable; deterministic findings
  stand; status cannot become PASS solely by omission.
- **External source outage** → check INCOMPLETE → ERROR/INCOMPLETE.
- **Bad input file** → visible validation error; case proceeds PARTIAL only
  when `allow_partial` (default true; `--strict` disables).
- **Idempotency** — identical inputs + config reproduce identical finding
  IDs; each run is stored separately.
- Logs: structured via `logging`, level from `SPECTRA_LOG_LEVEL`; raw
  submission payloads are not logged by default.
