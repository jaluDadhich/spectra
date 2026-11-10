# Configuration

All configuration is environment-based (`app/config/settings.py`). Copy
`.env.example` to `.env`. Secrets are read from the environment only and are
never logged, exported, or returned by the API.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | — | Required only when `SPECTRA_LLM_ENABLED=true` |
| `OPENAI_MODEL` | `gpt-6-luna` | Legacy fallback model setting |
| `OPENAI_FAST_MODEL` | `gpt-6-luna` | Intake and line-agent discovery, explanations, and issue-severity points |
| `OPENAI_REASONING_MODEL` | `gpt-6.1-sol` | Final whole-submission comparison and scope judgment |
| `OPENAI_FAST_REASONING_EFFORT` | `low` | Reasoning effort for fast tasks |
| `OPENAI_REASONING_EFFORT` | `high` | Reasoning effort for comparison tasks |
| `OPENAI_BASE_URL` | — | Optional compatible endpoint |
| `OPENAI_TIMEOUT_SECONDS` | `60` | Per-request timeout |
| `OPENAI_SCOPE_TIMEOUT_SECONDS` | `180` | Timeout per token-packed scope-judge batch |
| `OPENAI_MAX_RETRIES` | `3` | Bounded retries (retryable errors only) |
| `OPENAI_MAX_OUTPUT_TOKENS` | `2000` | Response cap |
| `OPENAI_SCOPE_MAX_OUTPUT_TOKENS` | `16000` | Completion cap per token-packed scope-judge batch |
| `OPENAI_MAX_REQUEST_TOKENS` | `25000` | Hard ceiling for estimated prompt plus completion tokens in each GPT call; completion allowance shrinks to fit |
| `SPECTRA_SCOPE_EVIDENCE_PAYLOAD_CHARS` | `42000` | Maximum cited-evidence bundle per scope-judge batch |
| `OPENAI_TEMPERATURE` | unset | Only if the model/endpoint supports it |
| `SPECTRA_LLM_ENABLED` | `false` | Master switch for GPT-assisted review |
| `SPECTRA_LLM_BUDGET_USD_PER_CASE` | `1.00` | Budget cap per case |
| `SPECTRA_MAX_LLM_CALLS_PER_CASE` | `12` | Call cap per case; includes a low-cost issue-severity points pass |
| `SPECTRA_MAX_CONCURRENT_LLM_CALLS` | `2` | Concurrency cap |
| `SPECTRA_MAX_EVIDENCE_PAYLOAD_CHARS` | `24000` | Max evidence bundle per task |
| `SPECTRA_PRICE_INPUT_PER_1K` / `…_OUTPUT_PER_1K` | Luna rates | Fast-model cost estimate |
| `SPECTRA_REASONING_PRICE_INPUT_PER_1K` / `…_OUTPUT_PER_1K` | Sol rates | Reasoning-model cost estimate |
| `SPECTRA_LOG_LEVEL` | `INFO` | Logging level |
| `SPECTRA_LOG_FILE` | `logs/spectra.log` | Local rotating pipeline log; relative paths resolve under the codebase (10 MiB per file, five backups) |
| `DATABASE_URL` | `sqlite:///./data/spectra.db` | Persistence (SQLite locally) |
| `SPECTRA_RULESET_DIR` | `./rulesets` | Ruleset JSON location |
| `SPECTRA_PROFILE_DIR` | `./profiles` | Document profile location |
| `SPECTRA_ACCOUNTS_DIR` | `<workspace>/documents` | Root folder with one subfolder per account (submission JSON files); drives `GET/POST /api/v1/accounts…` |
| `SPECTRA_API_TOKEN` | unset | When set, `/api/*` requires `Authorization: Bearer` |
| `SPECTRA_MAX_UPLOAD_BYTES` | `5000000` | Upload guard |
| `SPECTRA_EXT_*_ENABLED` / `…_API_KEY` | `false` | External source adapters (off by default) |
| `SPECTRA_WEB_SEARCH_ENABLED` | `false` | Allow live Tavily-compatible public-record/news search, only when no local `external_records` document is supplied |
| `SPECTRA_WEB_SEARCH_API_KEY` | — | Search provider key; sent only when live search is explicitly enabled |
| `SPECTRA_WEB_SEARCH_URL` | Tavily search endpoint | Search provider endpoint |
| `SPECTRA_WEB_SEARCH_CACHE_TTL_HOURS` | `168` | Local SQLite cache lifetime |

The API and CLI configure logging on startup. Review runs emit stage transitions,
document/evidence counts, deterministic-rule summaries, agent and external-tool
outcomes, GPT model/call timing and token counts, fallback decisions, final
finding counts, reviewer dispositions, and exports. Prompts, evidence values,
API keys, and reviewer rationales are not written to the log. Logs are local and
rotated; `logs/*.log*` is ignored by Git.

## Profiles (`profiles/*.json`)

Required-document profiles drive missing-document findings and freshness
rules. Requirements are scoped by `lines_of_business`. Add a carrier-specific
profile as a new JSON file and select it with `--profile <name>` or the API
`profile` field.

## Rulesets (`rulesets/*.json`)

Versioned rule registry: rule IDs, versions, severity, blocking flags,
applicability, tolerances. Validate with `spectra rules validate`. Findings
record the exact ruleset version in `config_versions` and per-finding
`rule_version`.

## API authentication

The API ships a boundary hook only: set `SPECTRA_API_TOKEN` for bearer-token
auth in shared environments. For multi-user production, put the service
behind your identity gateway and implement tenant/case scoping (see
`docs/limitations.md`). The UI is server-rendered; the API key is never sent
to browser JavaScript.
