# Delivery and measurement plan

Status: milestones 1–2 implemented offline; reliability, budget/cache,
evaluation, benchmark targets, and cloud deployment remain planned. See
[routing verification](milestone-2-verification.md) for evidence and limits.
Use the [task checklist](../openspec/changes/build-llm-reliability-gateway/tasks.md)
for progress and the [design](../openspec/changes/build-llm-reliability-gateway/design.md)
for assumptions. Capability specs, not this roadmap, define behavior.

## Gated milestones

| Milestone | Deliverable | Exit evidence |
| --- | --- | --- |
| 1. Foundation | Mock-backed authenticated generation/extraction, schema/error contracts, durable request evidence, local containers | Credential-free quickstart and offline API/schema/contract/health tests |
| 2. Multi-provider routing | Two adapters tested with sanitized fixtures, registry/pricing snapshots, deterministic router and audited control CLI | Same adapter suite for both providers; routing determinism/exclusion/configuration tests |
| 3. Reliability | Deadline/attempt bounds, classified recovery, shared circuits/admission, durable idempotency | Fault/concurrency/crash tests covering bounded recovery and uncertain execution |
| 4. Exact cache and budgets | Isolated validated TTL cache, fenced invalidation/single-flight, atomic reservations/reconciliation | Integrated concurrency, rollover, expiry, privacy, critical-dependency, and accounting checks |
| 5. Evaluation and observability | Versioned datasets/runs/reports/gates, summary API, telemetry and runbooks | Credential-free regression demo, traceable denominators, failed-promotion evidence, reproducible benchmark report |
| 6. Authorized staging | Reviewed AWS infrastructure, immutable rollout, secrets/retention, load/restore/rollback exercises | Explicit authorization, reviewed plan, deployment/security evidence, exercised rollback |

Adapters can be implemented and tested offline before live execution. Paid calls
remain disabled until reliability and budget gates pass and the owner explicitly
authorizes the provider workload/spending ceiling. Staging needs separate
account/region/resource authorization. No routine CI test should provision AWS.

The baseline is complete only after all applicable capability scenarios have
evidence, local demo/evaluation is reproducible, and the staging release/rollback
gate is exercised. A benchmark target is a mandatory promotion gate only if the
approved dataset/threshold profile explicitly makes it one.

## Benchmark methodology

Define workload/configuration before collecting numbers. Record dataset/content
hash, case count, task/difficulty mix, repetitions, concurrency, warm-up, run date,
runtime/image/code revision, region/environment, provider/model IDs and revision
limitations, prompt/schema/evaluator/policy/pricing versions, random settings,
deadline/attempt/budget bounds, cache mode/TTL, and uncertainty/missing data.
Store sanitized per-case evidence and aggregation scripts with the report.

Compare three conditions on the same approved workload:

1. A fixed strongest-model baseline, with a documented quality criterion and no cache.
2. Gateway routing with cache bypass, recording all retry/fallback spend.
3. Gateway routing with exact cache on a separately identified repeated workload.

Use identical provider settings and document differences in retry/timeout rules.
Report failed cases rather than charging only successful ones or dropping slow
results. Distinguish offline synthetic contract evidence from authorized live
quality/cost evidence; neither can silently stand in for the other. Report sample
sizes/uncertainty and avoid extrapolating beyond the measured workload.

## Targets retained from the handoff

These are aspirations, not results or universal SLOs:

- At least 99% schema-valid successful completions among all extraction benchmark
  cases, including timeouts/refusals/invalid results in the denominator. Any
  extraction returned as success must be validated, independent of that target.
- At least 99% successful recovery on a defined recoverable-transient-fault
  workload where an eligible healthy alternate and sufficient limits exist.
  Permanent outages or deliberately exhausted budgets are separate typed-failure
  checks, not cases expected to succeed through unlimited retry.
- p95 gateway overhead below 100 ms on a no-injected-fault workload. Measure
  ingress-to-terminal latency minus summed provider invocation wall time;
  queueing, cache/routing, validation, and critical writes remain gateway overhead.
  Report backoff separately for fault workloads and report end-to-end latency too.
- At least 20% lower estimated inference cost versus the fixed strongest-model
  condition while meeting the same approved task-quality gate. Include failed
  attempts and unknown-liability holds; report provider invoice discrepancies.
- At least 25% exact-cache hits on the declared repeated-request workload; do not
  generalize that hit rate to unrelated production traffic or semantic reuse.
- Every dispatched attempt has request/attempt/pricing linkage, including unknown
  usage status. Every production request retains minimal durable correlation
  evidence; detailed traces may be sampled.
- A reproducible clean-checkout local run and authorized clean-environment staging
  deployment, plus passing scoped secret scans and manual privacy/security review.

The handoff's suggested regression profile (schema validity at least 99%, task
accuracy no more than 2 percentage points below baseline, configured p95 and cost
ceilings) must be versioned and approved for the chosen dataset. Missing baseline
or required evidence blocks promotion. No fabricated baseline numbers are needed
to write or validate the planning documents.

## Release evidence and project summaries

Deliver API examples/OpenAPI, architecture/ADRs, threat model, sanitized dataset,
fault and quality reports, benchmark methodology/results, deployment/restore/
rollback guide, alert runbooks, test evidence, and a concise reproducible demo.
Trace capability scenarios to the actual checks, not just to task checkboxes.

For an eventual resume/interview summary, extract only built behavior and measured
outcomes. Cite the workload behind any percentage, distinguish personal ownership
from collaborators' work, and explain the tradeoff and failures. A missing number
is a reason to measure or use an honest qualitative statement, not to invent one.
