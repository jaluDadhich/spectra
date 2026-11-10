# Model & cost configuration guide

## Enabling GPT-assisted review

1. `SPECTRA_LLM_ENABLED=true`
2. `OPENAI_API_KEY=…` (environment only — never in source, prompts, fixtures,
   logs, or the UI)
3. Defaults route intake, line-of-business discovery, explanations, and quality
   issue-points to `gpt-6-luna` with low reasoning effort. The final cross-source
   comparison and scope judge use `gpt-6.1-sol` with high reasoning effort.
   Override with `OPENAI_FAST_MODEL`, `OPENAI_REASONING_MODEL`, and their
   `OPENAI_*_REASONING_EFFORT` settings. Structured output uses Chat
   Completions `response_format: json_schema` (strict).
4. Optional `OPENAI_BASE_URL` for a compatible endpoint/gateway.

## What the LLM does (and does not do)

The enabled GPT-first path reviews the whole evidence bundle and proposes
cross-document and within-document issues. Candidates are schema- and
evidence-validated; invalid citations trigger deterministic fallback. GPT
does not see evaluation labels and does not call tools/URLs from submitted
content. Deterministic mode remains available with `deterministic_only=true`.
The LangGraph workflow uses fast intake and line-specific discovery nodes, a
bounded fast critic pass, a strong final evidence-validation/judge stage, and a
low-cost issue-group points pass. Human
dispositions are stored as account-scoped memory for future agent runs.
External research reads a supplied `external_records` file first. If that file
is present, the research tool makes zero web requests. Otherwise live search
still requires `SPECTRA_WEB_SEARCH_ENABLED=true` and a configured key.

## Cost controls

| Control | Setting | Default |
|---|---|---|
| Master switch | `SPECTRA_LLM_ENABLED` | `false` |
| Call cap / case | `SPECTRA_MAX_LLM_CALLS_PER_CASE` | 12 |
| Budget / case | `SPECTRA_LLM_BUDGET_USD_PER_CASE` | $1.00 |
| Concurrency | `SPECTRA_MAX_CONCURRENT_LLM_CALLS` | 2 |
| Evidence payload cap | `SPECTRA_MAX_EVIDENCE_PAYLOAD_CHARS` | 24,000 |
| Output token cap | `OPENAI_MAX_OUTPUT_TOKENS` | 2000 |
| Timeouts / retries | `OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES` | 60s / 3 |

Cost estimates default to the published GPT-6 Luna and GPT-6.1 Sol rates and
can be overridden with the four `SPECTRA_*_PRICE_*` settings when choosing
different models. Confirm rates against your account and the current pricing
page; the budget cap uses estimates, not a quote.
External search provider costs, if any, are not included in OpenAI token-cost
estimates; live request counts and cache hits appear in the agent trace.

Design features that limit spend: review work is grouped (never one call per
field/row); evidence
bundles carry only normalized excerpts with stable IDs; explanations are
capped to the top findings; routine specialist discovery and unique issue-group
quality scoring use the low-cost model.

## Failure behavior

- Auth error / persistent failure → the review stage records the reason and
  deterministic results are used as fallback.
- Budget/call cap reached → remaining semantic tasks skip with a note.
- `deterministic_only=true` (CLI flag or API field) → zero LLM calls; the
  semantic stage reports `SKIPPED` with the reason.
