## Context

See [proposal.md](proposal.md) for motivation and capability scope. At proposal
creation the repository had no application code or API/data migrations. Milestone
1 now supplies the offline foundation; see [its verification report](../../../docs/milestone-1-verification.md).
Milestones 2–5 now add offline routing, reliability, budgets, exact-cache,
synthetic evaluation, and bounded observability evidence. Deployment decisions
remain planned. The historical handoff
is preserved in [the reference snapshot](../../../docs/reference/original-handoff.md).
The capability files under `specs/` describe the full planned contract; only
checked tasks have implementation evidence.

This design is required because durable spend/idempotency state, concurrent
execution, provider failures, privacy, and deployment cross several boundaries.
The choices below refine the handoff into an implementable baseline; they are
proposed project defaults, not claims that an owner has approved every detail.

## Goals / Non-Goals

Goals: maintain explicit state ownership, bound the synchronous path, keep
provider-specific behavior inspectable, and make offline fault tests the primary
development loop. Prefer correctness evidence over the number of integrations.

Non-goals: a fleet of microservices, an orchestration framework, automatic paid
tests/deployments, or approximate cache reuse inside the baseline. Follow the
proposal for product-level exclusions.

## Decisions

### 1. Single service with inward-facing domain contracts

Use Python 3.12, FastAPI/Pydantic and asyncio as proposed in the handoff, with
PostgreSQL for correctness state and Redis for ephemeral controls/cache. Pin
dependency versions during implementation rather than assuming current SDK APIs.
Keep domain policies/results independent of SDK/HTTP/ORM types. API handlers
compose routing, execution, validation, and persistence through explicit
interfaces. This permits deterministic adapters and fake clocks in unit tests.

Alternative: adopt a large agent/orchestration framework. It adds behavior this
gateway does not require and makes retry/usage ownership harder to inspect.
Alternative: split routing/evaluation into services now. Defer until measured
capacity or isolation needs justify distributed coordination.

Planned code layout, created only as corresponding work begins:

```text
app/
  api/              Routes, public schemas, and error mapping
  domain/           Provider-neutral inputs/results and immutable snapshots
  providers/        SDK adapters and scripted mock
  routing/          Candidate filtering/ranking and explanations
  reliability/      Deadlines, attempts, circuits, and admission
  cache/            Exact identity, TTL, invalidation, and single-flight
  usage/            Pricing, reservations, and reconciliation
  evaluation/       Dataset loading, execution, scorers, reports
  observability/    Logs, metrics, traces, and authorized summaries
  security/         Credential verification, permissions, redaction
  persistence/      Repositories, transactions, and protected content
tests/              Unit, API, contract, integration, fault, security, load
datasets/           Synthetic/approved versioned cases and manifests
migrations/         Forward-compatible schema changes
deploy/             Local/staging configuration and smoke scripts
infra/terraform/    Reviewed AWS infrastructure
```

### 2. Explicit API and safe defaults

The normative API is [gateway-api](specs/gateway-api/spec.md). Publish OpenAPI
before live adapters. `input` is a nonempty string. `model_policy` identifies a
configured name or pinned version; omission resolves the default at acceptance.
Quality tiers are `economy`, `standard` (default), and `high`; task types are
`generation`, `extraction`, `classification`, and `summarization`. Extraction
fixes its task type to extraction. Cache modes are `bypass` (default),
`read_only`, and `read_write`; there is no semantic mode.

Initial configurable defaults: temperature 0, maximum output 512 tokens,
deadline 30,000 ms (allowed 100–120,000 ms), body limit 256 KiB, input limit
100,000 characters, and metadata at most 20 short string pairs. A request may
omit its cost ceiling only when a server-side per-request ceiling exists;
effective allowance is the minimum of caller and server limits. Require positive
decimal-string USD ceilings, positive output bounds, and bounded keys (128
characters). Actual model context/output limits can be stricter than API limits.
Document all limits in OpenAPI and test edge values rather than silently clipping.

Use JSON Schema draft 2020-12 with a documented bounded subset initially:
objects/arrays/scalars, required fields, enum, numeric/string/array limits, and
local `$defs`/references. Disable remote references; initially reject unsupported
composition/regex keywords rather than pretending they are enforced. Limit inline
schema size to 32 KiB and resolved depth to 16, including reference cycles.
Named schemas are immutable name/version/content-hash records. Expose JSON only
after local validation even if a provider advertises native structured output.

Normalized finish reasons are `stop`, `length`, `refusal`, and `unknown`.
Extraction `length`/`refusal` outcomes are unsuccessful, even if parseable JSON
appears. Refusal does not automatically trigger safety-policy evasion through
another provider. Baseline schema validation recovery is another invocation with
the same input/schema and sanitized corrective instruction, not generated-code
execution. Do not put raw validation input into logs.

Success `usage` contains current execution totals, availability/completeness,
attempt count, and separately labeled source provenance for cache/replay. Record
`original_request_id` on replay and retain original execution latency separately
from new ingress latency. Cache/replay source cost is never fresh provider spend.

Error codes/statuses: `UNAUTHENTICATED` 401; `FORBIDDEN` 403; `INVALID_REQUEST`
and `INVALID_SCHEMA` 422; `PAYLOAD_TOO_LARGE` 413; `RATE_LIMITED` and
`BUDGET_EXCEEDED` 429; `IDEMPOTENCY_CONFLICT`, `REQUEST_IN_PROGRESS`, and
`EXECUTION_UNCERTAIN` 409; `NO_ELIGIBLE_MODEL`, `PROVIDER_UNAVAILABLE`, and
`DEPENDENCY_UNAVAILABLE` 503; `OUTPUT_VALIDATION_FAILED` 502;
`DEADLINE_EXCEEDED` 504. Permanent invalid upstream requests use sanitized
`UPSTREAM_REQUEST_REJECTED` 502, not a retryable 5xx. Document the distinction
between upstream retry classification and safe client retry. Error precedence:
deadline expiry first, then blocking spend/critical-state failure, then final
output validation failure, then unavailable-provider exhaustion.

### 3. Request state and durable critical path

Request flow:

```text
Authenticate + validate + rate/concurrency admission
  --> Resolve schema, policy, registry and pricing snapshot
  --> Acquire keyed idempotency ownership (when requested)
  --> Try eligible exact cache
  --> Rank candidates
  --> Reserve spend + persist dispatch intent
  --> Invoke provider within remaining deadline
  --> Classify/validate + reconcile observed or uncertain usage
  --> Repeat only within all limits
  --> Persist terminal outcome --> respond
```

Each retry is a new attempt with its own ID; an attempted fallback is never hidden
inside an SDK. Disable SDK automatic retries or explicitly include them in the
gateway's attempt/usage bounds. PostgreSQL owns requests, attempts, immutable
versions, spend reservations, usage events, idempotency state, and audit events.
Use unique event/attempt identities and transactions for replay-safe accounting.
Store dispatch intent before sending bytes upstream. If dispatch/outcome is
ambiguous, recovery treats it as potentially billed, even when it may never have
reached the provider. This is conservative rather than an exactly-once claim.

Core entities extend the handoff with `spend_reservations`, `idempotency_records`,
`schema_versions`, `dataset_versions`, and protected result/artifact references.
Record token/cost availability, pricing version, input hash, exclusion reasons,
and reconciliation state. Use UTC period buckets and decimal numeric columns.
Application identity comes from credential/configuration, not free-form metadata.

Alternative: asynchronously emit all analytics/usage after returning success.
Reject for accounting/idempotency correctness; only non-critical telemetry is
best effort. If terminal persistence fails after a billed response, do not return
an unrecorded success; retain uncertain dispatch evidence and fail with a typed
error. Reserve time for recording and apply bounded I/O timeouts. Recovery may
run after the client deadline, but cannot dispatch another provider attempt.

### 4. Shared reliability controls and idempotency

Use one monotonic deadline starting at gateway receipt. Per-attempt connect/read
timeouts and a configurable recording margin (initially 100 ms) share remaining
time. Initial local scheduler tolerance is 50 ms for fake/loopback deadline tests;
provider cancellation cannot guarantee cessation of upstream work or billing.
Default retry bounds are two retries per candidate and four total invocations.
Backoff starts at 100 ms, caps at 2 seconds, and uses injectable full jitter.

Redis owns cross-replica tenant/provider admission and circuit/probe leases.
Initially use five transient failures in a 60-second window, a 30-second open
cooldown, and one half-open probe per provider domain. Credential failure disables
that candidate until configuration correction; client schema failures do not
trip provider circuits. Document configured 429/transient failure counting.
Capture health for deterministic ranking but recheck live admission/provider
disablement before each attempt. Registry refresh delay is at most 5 seconds.

Rank eligible models by a policy-weighted sum of quality, affordability, expected
latency, and health scores normalized to [0, 1] using fixed policy bounds, not
request-dependent candidate-set scaling. Record raw inputs and weights. Break
ties by stable provider/model ID order; seed initial quality/latency scores from
configuration until versioned evaluation evidence is available.

Idempotency is opt-in via the body key. Fingerprint authenticated application,
endpoint, caller fields/defaults, resolved schema content, and constraints; retain
the first resolved policy/pricing snapshot for that operation. Encrypt terminal
replay content and retain records/results for 24 hours by default. Identical
completed/failed operations replay their terminal result; failures before an
execution record exists are not replayable. Never resurrect an uncertain
operation just because an execution lease expired. Resolve it through provider
evidence or conservatively charged/manual recovery. After expiry, a key is new
and can incur new spend; document this plainly to clients.

Alternative: Redis-only idempotency or timeout-triggered redispatch. Either can
lose ownership evidence during restarts and duplicate billable work. PostgreSQL
is the durable authority; leases accelerate detection but cannot prove an
upstream operation did not happen.

### 5. Spend reservations, not invoice guarantees

Use adapter-specific tokenizers or conservative upper bounds, configured maximum
outputs, and immutable prices to reserve each attempt. Reject strict-budget
execution for a model whose token/cost upper bound is unavailable. Atomically
lock tenant UTC budget buckets and request allowance before committing a
reservation; committed spend plus outstanding holds determines capacity.
Continue accounting in the original period bucket after a midnight/month rollover.

On known usage, reconcile once and release the confirmed difference. On unknown
usage, hold the bound and run recovery; the default unresolved recovery policy
is a conservative charge with an audited reason, not an automatic release.
Record overruns when reported usage/pricing contradict assumptions, alert, and
disable unsafe estimates for later requests. Report these as estimated liability
controls rather than a guarantee about changing upstream invoices.

Alternative: check daily totals after a call. It races under concurrency and
cannot bound retry/fallback spend. Cheap fallback remains subject to the original
quality and accumulated request allowance; exhausted budgets are not bypassed.

### 6. Exact cache first; semantic reuse is a separate extension

Use canonical serialization and keyed hashes without rewriting prompt text.
Key tenant/application, endpoint/task, parameters, schema hash, policy/registry
snapshot, and server-managed application cache generation. Model-specific routing
settings live inside the registry/policy snapshot. A cache result still requires
current model/tenant/quality checks and local schema validation.

Caching requires explicit mode and policy eligibility (temperature 0, approved
non-sensitive classification). Even temperature 0 is not a provider guarantee
of determinism; exact caching intentionally reuses a prior output. Default TTL
is one hour; retention caps constrain configured TTL. Protect cached content in
transit/at rest with application-level encrypted envelopes. Use fencing tokens
for single-flight leases and generation checks on invalidation/writes. Exact-key
invalidation uses a per-key generation/tombstone as well, so a slow in-flight
writer cannot undo an acknowledged exact deletion.

Milestone 4 implements cache content and fenced leases in an optional Redis
connection, while PostgreSQL stores durable tenant approval and invalidation
generations. The request carries an explicit non-sensitive classification
attestation; tenant approval is a separate audited operator decision, not an
automatic classifier. The local synthetic tenant is approved only for its
fixture workflow. Requests begun before invalidation may finish execution,
but new-generation reads cannot see an old-generation write.

Authenticated operator CLI commands initially manage exact-key invalidation and
namespace generation. The CLI updates durable configuration and coordinates the
cache; acknowledgment waits until the new generation is visible. Configuration
administration uses the same validated/audited path as policy changes. No public
configuration-write HTTP API is required initially.

Alternative: embedding similarity caching. The handoff lacked an embedding model,
threshold, candidate-index design, schema/tenant filtering, adversarial tests,
and false-hit quality gate. Do not call exact hashing semantic caching. A future
change must specify those contracts and compare false hits and privacy risks
before this extension is promoted.

### 7. Evaluation and observability without silent evidence gaps

Store immutable sanitized dataset manifests and approved baseline/threshold
profiles. A separate runner process from the same service image claims durable
runs/cases; start with one worker, not a new queue service. Restart transitions
unresolved cases to interrupted/uncertain states; no blind paid rerun. Local
artifacts use a protected filesystem store; authorized AWS staging uses private
S3. Presigned links, if used, are short-lived and issued only after authorization.

Scores/thresholds are task-specific. Default benchmark intentions are in
[the roadmap](../../../docs/roadmap.md), not hard-coded universal SLOs. Record
runtime/image, versions, missing data, mutable-model limitations, and all failure
denominators. Synthetic offline tests supply CI evidence; live-quality evaluation
is explicit and budgeted, never secretly substituted for fixtures or vice versa.

Use OpenTelemetry spans and bounded Prometheus-compatible metric labels; never
put request IDs, arbitrary tenant metadata, or prompt text in metric labels.
Structured logs can hold non-sensitive request/tenant IDs. Operator summaries
read durable records/approved aggregates; traces may be sampled while minimum
request evidence is durable. Dashboard UI is optional because the summary/query
API provides the initial operator surface.

### 8. Dependency failure and privacy boundaries

PostgreSQL failure before dispatch fails closed. Redis admission/circuit-control
failure also fails closed: an optional cache failure does not authorize bypassing
rate/concurrency controls. A separately failing cache connection/store may be
bypassed if those controls remain healthy. Telemetry exporter failure is best
effort and observable. Live provider outages affect routing, not liveness.

No raw production content is retained by default except explicit cache opt-in or
keyed replay, which necessarily require short-lived protected results. Default
retention: cache one hour, keyed replay 24 hours, operational metadata/audit
evidence 30 days, detailed logs/traces 7 days, approved evaluation artifacts 30
days. Make these configurable, document deletion/backup limitations, and test
expiry. No production-content sampling in the baseline; online/debug sampling
requires explicit owner policy approval and sanitized bounded artifacts.

## Risks / Trade-offs

- [Conservative reservations reject affordable requests] --> Prefer bounded
  liability; improve tokenizer/usage evidence rather than silently relaxing caps.
- [Retries can multiply upstream billing] --> Include every attempt and uncertain
  hold in accounting; expose this limitation to callers.
- [Critical writes add latency] --> Bound transactions, reserve recording time,
  benchmark overhead, and keep exporters off the critical path.
- [Shared Redis controls reduce availability] --> Fail closed on lost control
  state and test recovery; document availability versus isolation tradeoff.
- [LLM outputs and provider aliases drift] --> Record reproducibility limits,
  version evidence, review sample, and evaluated rollback policy.
- [Encrypted cache/replay still retains content] --> Explicit opt-in/key semantics,
  short retention, scoped access, deletion checks, and no secret-bearing fixtures.
- [Program-level change is large] --> Apply gated milestones, not all cloud and
  adaptive extensions at once; do not archive a partially implemented program.

## Migration Plan

There is no legacy service to migrate. Build the offline vertical slice, then
adapters/routing, reliability, cache/accounting, evaluation, and authorized staging
in the milestone order in `tasks.md`. Minimal durable accounting is required
before any live-provider attempt; budget/correctness is not postponed to a demo.

For staging, use the handoff's ECS Fargate/ALB, private RDS/ElastiCache, S3,
Secrets Manager, IAM, and Terraform approach. Review plans and cost limits before
apply. Pin image digests and active policy versions. Prefer expand/contract
migrations and retain old-version compatibility for rollback; never automatically
reverse data migrations. Exercise image/policy rollback and restore procedure
in staging, then publish evidence. No production deployment is implied by this plan.

## Open Questions

These are deferrable parameters inside the specified boundaries, not unresolved
product scope:

- Which two provider/model IDs and SDK versions will populate the registry?
  Choose before milestone 2 against the already defined contract/capabilities.
- Which sanitized dataset cases and tenant-specific budget/threshold values will
  be approved? Choose before evaluation promotion; missing profiles block gates.
- Which AWS account, region, retention overrides, and spending ceiling will be
  authorized for staging? Choose before any Terraform apply/live smoke test.
