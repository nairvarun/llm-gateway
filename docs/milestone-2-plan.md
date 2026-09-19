# Milestone 2 implementation plan

Scope: OpenSpec tasks 2.1–2.7 only. Milestone 1 behavior remains available and
is rerun after every routing change. Live provider dispatch stays disabled until
the reliability and budget gates of milestones 3–4 are complete. This plan does
not amend the capability specifications.

1. Record two model IDs, exact SDK versions, capabilities, source-checked prices,
   context/output limits, and the provenance of synthetic wire fixtures.
2. Add OpenAI and Anthropic adapters behind the provider-neutral protocol. Use
   injected SDK clients with automatic retries disabled, no tools, no streaming,
   bounded request parameters, and sanitized failure classification. Run the
   same parametrized contract tests against both adapters and the mock with no
   credentials or network calls.
3. Define immutable model/policy/pricing payload validation and transactional
   resolution. Require a pinned version per accepted request. Compute decimal
   upper estimates from a defensible token bound; reject strict-budget routing
   when the bound is unavailable rather than guessing.
4. Filter against all required constraints, then rank with normalized fixed
   policy bounds and stable provider/model tie-breakers. Test identical-snapshot
   determinism and every exclusion reason.
5. Add operator-only publish/activate/rollback/disable commands with validated
   payloads and audit events. Preserve in-flight snapshots and enforce the
   documented refresh limit before another attempt.
6. Persist and expose sanitized routing evidence by request ID, including
   selected versions, candidate order, and exclusions. Keep provider wire types
   out of OpenAPI. Re-run all milestone 1 checks and browser/smoke tests.

Completion means all 2.x checkboxes have code and test evidence; a document or
adapter fixture alone does not justify marking the whole milestone complete.
