## Why

Direct LLM-provider integrations expose applications to inconsistent contracts,
transient failures, invalid structured output, and poorly attributed spend.
This greenfield project will make those failure modes explicit and measurable;
the informal handoff needs a testable contract before implementation begins.

## What Changes

- Introduce a tenant-authenticated, versioned generation/extraction API with
  normalized metadata, typed errors, and bounded schema validation.
- Add two live-provider adapters plus a deterministic fault-injectable mock,
  deterministic routing, shared deadlines, bounded retry/fallback, and circuits.
- Add tenant-scoped idempotency, exact caching, concurrent spend reservations,
  attempt-level usage attribution, and privacy-safe operational evidence.
- Add versioned evaluation datasets, task-specific scores, promotion gates,
  reproducible local deployment, and a gated AWS staging deployment.
- Treat all work as planned: this change contains no implemented behavior.
  Use milestone gates rather than presenting the whole platform as an MVP.

## Capabilities

### New Capabilities

- `gateway-api`: authenticated generation/extraction, normalized contracts,
  schema limits, typed errors, and health behavior.
- `provider-routing`: provider abstraction, model registry, deterministic
  candidate ranking, policy versioning, and operator configuration.
- `request-reliability`: deadlines, retry/fallback, circuit breakers,
  concurrency limits, and scoped idempotency.
- `response-cache`: opt-in exact response caching, versioned isolation,
  invalidation, expiration, and stampede protection.
- `usage-budgets`: versioned cost estimates, atomic spend reservations,
  reconciliation, and durable per-attempt accounting.
- `evaluation-observability`: reproducible evaluation runs, regression gates,
  telemetry, operator summaries, and retention-aware evidence.
- `deployment-security`: local/CI/staging reproducibility, secret handling,
  access boundaries, auditability, threat modeling, and rollback.

### Modified Capabilities

None. There are no existing durable capability specs or application code.

## Impact

Future additions affect the FastAPI service, PostgreSQL/Redis state, provider
adapters, evaluation artifacts, CI, Docker, and Terraform-managed AWS staging.
SDK/provider selection and account-specific AWS settings require decisions
before their milestone; this proposal does not authorize paid calls or provisioning.

### Non-goals

No chatbot UI, general RAG/agent framework, training, every provider/modality,
streaming, OpenAI compatibility, adaptive routing, multi-region deployment, or
Kubernetes in the baseline. Semantic caching remains a separately gated extension
because approximate reuse needs a quality/isolation contract, not just key hashing.
No claim of exactly-once upstream execution, guaranteed actual billing ceilings,
or LLM-judge ground truth. Portfolio metrics are targets until measured.
