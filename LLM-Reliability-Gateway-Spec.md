# LLM Reliability Gateway

Status: proposed build contract; documentation-only repository.
This is the entry point to the refined specification, not evidence of a running
service. It supersedes the informal handoff preserved unchanged in
[docs/reference/original-handoff.md](docs/reference/original-handoff.md).

## Objective

Build a provider-neutral Python gateway that makes LLM-dependent applications
more reliable, observable, spend-aware, and evaluatable. The engineering story
must be supported by repeatable fault tests and benchmarks, not a list of tools
or unsupported resume claims.

## Baseline scope

- Authenticated versioned text generation and validated structured extraction.
- Two live-provider adapters and a deterministic fault-injectable mock.
- Explainable versioned routing with capability, quality-tier, health, deadline,
  tenant, and estimated-spend constraints.
- Bounded retries/fallback, shared circuits/admission, and tenant-scoped
  idempotency with explicit uncertain-execution behavior.
- Opt-in exact caching with TTL, isolation, invalidation, and single-flight.
- Durable per-attempt usage, versioned decimal estimates, concurrent spend
  reservations, and reconciliation.
- Authorized evaluation runs, task-specific reports/gates, privacy-safe
  correlation evidence, metrics, traces, and operator summaries.
- Reproducible offline development/CI, followed by explicitly authorized AWS
  staging with security, deployment, and rollback evidence.

These capabilities form the full baseline program, not the first milestone.
The first delivery is an offline mock-backed generation/extraction slice.
See the [roadmap](docs/roadmap.md) for incremental gates.

## Normative capability specifications

The active OpenSpec change is
[build-llm-reliability-gateway](openspec/changes/build-llm-reliability-gateway/proposal.md).
Its capability specs define planned observable behavior and testable scenarios:

| Capability | Contract |
| --- | --- |
| [gateway-api](openspec/changes/build-llm-reliability-gateway/specs/gateway-api/spec.md) | Authentication, API fields, validated extraction, errors, health |
| [provider-routing](openspec/changes/build-llm-reliability-gateway/specs/provider-routing/spec.md) | Adapter behavior, registry/policy snapshots, candidate ranking, configuration |
| [request-reliability](openspec/changes/build-llm-reliability-gateway/specs/request-reliability/spec.md) | Deadlines, attempts, circuits, concurrency, idempotency |
| [response-cache](openspec/changes/build-llm-reliability-gateway/specs/response-cache/spec.md) | Exact identity, privacy, TTL, validation, invalidation, single-flight |
| [usage-budgets](openspec/changes/build-llm-reliability-gateway/specs/usage-budgets/spec.md) | Attempt usage, conservative reservations, unknown usage, reconciliation |
| [evaluation-observability](openspec/changes/build-llm-reliability-gateway/specs/evaluation-observability/spec.md) | Run lifecycle, scoring, gates, telemetry, summaries |
| [deployment-security](openspec/changes/build-llm-reliability-gateway/specs/deployment-security/spec.md) | Local/CI/staging reproducibility, secrets, retention, access, rollback |

There are no durable capability specs under `openspec/specs/` yet. Do not sync
or archive the initial program merely because its planning files validate.

## Planned API surface

- `POST /v1/generate`: normalized text response and current execution metadata.
- `POST /v1/extract`: locally validated JSON under an inline or named versioned schema.
- `POST /v1/evaluations/runs`: authorized durable run creation.
- `GET /v1/evaluations/runs/{run_id}`: run status, scores, gates, artifact references.
- `GET /v1/metrics/summary`: authorized bounded-window operational summary.
- `GET /health/live` and `GET /health/ready`: liveness and sanitized readiness.

OpenAPI will define exact wire types/defaults in milestone 1. Capability specs
are the behavior contract; the [design](openspec/changes/build-llm-reliability-gateway/design.md)
records proposed stack, defaults, state ownership, and control paths. Configuration
changes/invalidation initially use a validated authorized operator CLI; an
operator dashboard or public configuration-write API is not required.

## Important limits and safety invariants

Extraction never succeeds with unvalidated, truncated, or refused output.
Retry/fallback cannot weaken caller constraints. A single overall deadline and
total invocation cap cover all recovery attempts.

Idempotency prevents duplicate gateway execution while ownership/evidence is
known; a timeout cannot prove upstream execution or billing did not happen.
Uncertain keys remain blocked pending reconciliation. Spend limits bound admitted
estimated liability using conservative reservations; they cannot guarantee an
unchanging provider invoice. Missing usage is unknown, never silently zero.

Raw production prompts/outputs are not retained by default. Opted-in caching and
keyed replay necessarily retain short-lived encrypted result content with explicit
retention. Tenant identity comes from authentication, never metadata. Critical
accounting/idempotency/admission failures stop execution; optional telemetry loss
does not. The [handoff review](docs/handoff-review.md) explains these refinements.

## Deferred scope

Semantic caching requires a separate quality/privacy contract and benchmark.
Streaming, OpenAI-compatible chat, adaptive/canary routing, queue-backed batch
workers, multi-region failover, dashboard UI, formal SLOs, and Kubernetes are
extensions, not baseline requirements. No general RAG platform, autonomous-agent
framework, fine-tuning, or universal provider/modality support is planned.

## Delivery and evidence

Use the [unchecked milestone tasks](openspec/changes/build-llm-reliability-gateway/tasks.md)
and [measurement plan](docs/roadmap.md). Release evidence must trace implemented
behavior to capability scenarios, including failure cases, security boundaries,
and an exercised staging rollback. Numeric performance/quality goals are targets
until measured against a documented workload and approved gate profile.

`sources/` remains read-only synced reference material. No candidate resume is
present. Any future project/resume summary must distinguish built behavior,
measured outcomes, planned extensions, and unresolved limitations.
