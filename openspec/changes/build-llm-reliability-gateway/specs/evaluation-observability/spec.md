## Purpose

Make operational outcomes diagnosable and model/policy changes reproducibly
evaluatable without confusing benchmark targets with measured quality evidence.

## ADDED Requirements

### Requirement: Durable authorized evaluation runs

`POST /v1/evaluations/runs` SHALL accept an authorized immutable dataset version,
model list, policy version, and threshold profile, return 202 with `run_id`, and
execute outside the generation request's synchronous path. Runs SHALL publish
queued/running/completed/failed/cancelled states through
`GET /v1/evaluations/runs/{run_id}`. Cases SHALL use the same request/spend controls
as normal execution and disable cache by default. Restarted/interrupted work
SHALL retain per-case evidence and SHALL not automatically redispatch uncertain
cases. Tenant clients SHALL not access another tenant's run/artifacts.

#### Scenario: Interrupted evaluation worker

- **WHEN** execution stops mid-run with an unresolved case attempt
- **THEN** the run exposes the interruption and preserves uncertain-case state rather than reporting complete or blindly repeating billed work

### Requirement: Reproducible task-specific scoring

Runs SHALL record dataset/content hash, code revision, evaluator/prompt/schema
versions, provider/model identifier, policy/pricing versions, sampling settings,
case outcomes, and human-review status where applicable. Reports SHALL record
model alias/revision limitations and SHALL not promise identical outputs from
mutable upstream models. Scorers SHALL use extraction validity/field accuracy,
classification precision/recall/F1, or documented generation rubrics according
to task. Failure/timeouts SHALL remain in documented denominators; LLM judges
SHALL be labeled fallible and SHALL not replace required human review.

#### Scenario: Failures in an extraction benchmark

- **WHEN** some cases time out or produce invalid JSON
- **THEN** the report includes them in total cases and publishes validity/accuracy denominators instead of silently dropping them

### Requirement: Versioned regression and promotion gates

Comparison reports SHALL identify the approved baseline and threshold profile,
quality deltas, latency, estimated cost, and per-case evidence. Missing baseline,
missing evidence, or violated required thresholds SHALL block promotion.
Schema-validity, task-accuracy tolerance, p95 target, and spend limits SHALL be
configured per dataset/task and versioned. Performance targets SHALL NOT appear
as achieved results before the associated workload has been measured.

#### Scenario: Quality improves but cost regresses

- **WHEN** quality passes but cost exceeds the approved threshold
- **THEN** the gate fails and the previous active policy remains selected

### Requirement: Privacy-safe correlated operational telemetry

The gateway SHALL produce structured logs, metrics, and traces linking ingress,
cache/routing decisions, attempts, validation, and durable recording by request
ID. Metrics SHALL include requests/outcomes, latency distributions, retries,
fallbacks, timeouts, circuits, cache outcomes, validation failures, usage status,
tokens, and estimated cost, with bounded label cardinality. Raw prompts, outputs,
keys, and personal metadata SHALL not be logged by default. Optional exporter
failure SHALL not prevent otherwise valid responses; exporter loss SHALL itself
be observable. Every production request SHALL retain minimal durable correlation
evidence even when detailed traces are sampled.

#### Scenario: Trace sampling excludes a request

- **WHEN** a request is not selected for a detailed trace
- **THEN** its durable request/attempt evidence remains queryable by request ID

### Requirement: Authorized summaries and alert evidence

`GET /v1/metrics/summary` SHALL return a bounded UTC time-window summary of
volume, success rate, p50/p95 latency, provider errors, fallback/cache rates,
tokens, estimated cost, and unknown-usage counts in authorized scope. Summary
definitions SHALL distinguish gateway requests, provider attempts, replays,
cache hits, and evaluation traffic. Alerts SHALL have thresholds, observation
windows, and runbooks for elevated failures/latency/spend, validation failures,
open circuits, exporter loss, and exhausted budgets.

#### Scenario: Tenant summary excludes other workloads

- **WHEN** a tenant requests its application-traffic summary
- **THEN** it receives only authorized application traffic, with evaluation traffic and source cache usage excluded from fresh provider totals
