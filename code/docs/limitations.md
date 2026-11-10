# Limitations and production-readiness checklist

## Known limitations

1. **Synthetic fixtures only.** `samples/` contains fabricated data; the
   perfect evaluation scores on A01/A02/A03 measure the rules against
   fixtures designed to exercise the taxonomy. They say nothing about
   performance on real submissions. Real JSON extracts and carrier rule
   configuration are required before any production claim.
2. **SQLite persistence.** The bundled repository is a local-prototype
   store. It is not suitable for concurrent multi-user production; put a
   migration-ready RDBMS behind `CaseRepository`'s narrow interface.
3. **External search is general-purpose, not source-verified.** A Tavily-
   compatible search connector is opt-in and local `external_records` always
   suppresses outbound calls. Search results are leads with URLs and retrieval
   timestamps; there are no direct OSHA, FMCSA, or state-registry API adapters,
   and search-result identity must be verified by a human.
4. **No carrier rates/rules.** Tolerances, severity mappings, and the
   required-document profile are defaults, not filed carrier values. Premium
   impact is never estimated; findings say "impact not calculated — approved
   rating inputs/rules unavailable" by design.
5. **API auth is a hook.** `SPECTRA_API_TOKEN` is a single shared bearer
   token. Production needs SSO/RBAC, per-tenant isolation, and rate limiting
   at the deployment boundary.
6. **Synchronous execution.** Reviews run in-request. Large submissions
   should move execution to a task queue behind the same API contract.
7. **Coverage-fit is data-level.** Without carrier-approved form wording,
   P11 findings state the limitation explicitly; no legal interpretation is
   produced.
8. **Entity resolution is heuristic-conservative.** Unresolved matches
   produce review questions, not conclusions; strong registries improve this.
9. **Evaluation harness scope.** Matching is by taxonomy/subtype; deeper
   evidence-relationship matching (specific claim IDs, locations) is future
   work, as are hard-negative fixture packs at scale.

## Production-readiness checklist

- [ ] Carrier-approved rulesets, tolerances, profiles, severity policy
- [ ] Approved external-source adapters with licensing review
- [ ] RDBMS + migrations; backup/retention policy; append-only audit storage
- [ ] SSO/RBAC + tenant isolation; secrets in a managed store; rate limiting
- [ ] Async execution queue; horizontal scaling; SLOs
- [ ] Calibrated confidence (only after measured outcome data exists)
- [ ] Real-data evaluation with per-family precision/recall reporting
- [ ] Dependency vulnerability scanning in CI (e.g. pip-audit)
- [ ] DPIA/privacy review for PII minimization and retention
