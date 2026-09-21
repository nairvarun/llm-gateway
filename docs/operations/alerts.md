# Offline alert rules and response

`deploy/alert-rules-v1.json` is a pinned, reviewed rule set for an observation
system, not a claim that local Docker or cloud alert delivery is configured.
The `gateway evaluate-alerts` CLI accepts sanitized numeric signals and their
actual UTC observation window, checks the manifest hash, and emits only names,
thresholds, measured values, and runbook links. A missing signal or too-short
window does not pass as a healthy measurement; it is reported as missing
evidence. Staging wiring and delivery need separate authorization.

All rates use the declared window and explicit denominators. `failure_rate`
counts failed gateway requests among accepted application requests;
`validation_failure_rate` counts rejected extraction outcomes among extraction
attempts. `p95_latency_ms` is ingress-to-terminal. `daily_used_fraction` and
`remaining_budget_fraction` use committed plus held liability and the
authenticated tenant's current UTC daily limit. `open_circuits` counts currently
open provider domains; `exporter_failures` counts failed best-effort exports.
Never put tenant IDs, prompt text, keys, output, or arbitrary metadata into
Prometheus labels or alert messages.

## Elevated failures

Check `/health/ready`, the scoped summary, and sanitized request IDs. Separate
provider faults from validation, admission, budget, and critical-state errors.
Keep the critical Redis/PostgreSQL fail-closed behavior. Roll back an unsafe
policy through the audited operator command; do not increase retries blindly.

## High latency

Compare p95 ingress latency with provider-attempt and admission timing. Check
queueing, database writes, deadlines, and exporter loss separately. A slow
provider may continue billable work after timeout; inspect uncertain holds.

## High spend

Inspect `/v1/spend` committed and held amounts, per-attempt pricing versions,
and recent retries/fallbacks. Disable unsafe models through the audited control
path. Never release an unknown-usage hold merely to restore capacity.

## Validation failures

Verify pinned schema/prompt/model versions and sanitized per-case outcomes.
Inspect refusal/truncation separately. Do not return invalid JSON as a success
or loosen an approved schema just to pass a benchmark.

## Open circuits

Inspect classified transient faults and Redis health, then allow the fenced
half-open probe after cooldown. Do not bypass shared admission or send a blind
burst of retries.

## Exporter loss

Check the optional collector endpoint and `gateway_exporter_failures_total`.
Durable request/attempt evidence remains the authority. Restore the collector
without blocking otherwise valid responses; investigate gaps in sampled traces.

## Budget exhaustion

Inspect current UTC day/month buckets and outstanding reservations. Notify the
tenant/operator of exhausted estimated liability. Reconcile unknown attempts
with an audited conservative charge; do not reset periods or treat timeouts as
free work. A higher limit is an explicit operator decision.
