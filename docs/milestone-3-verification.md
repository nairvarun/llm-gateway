# Milestone 3 verification — offline reliability

Verified locally on 20 September 2026. OpenSpec tasks 3.1–3.8 are complete in
the mock-backed runtime; the full program remains active. No provider credentials,
paid calls, cloud resources, deployment, quality benchmark, or production SLO
evidence were used.

## Delivered behavior

- One monotonic deadline covers schema/registry reads, shared admission, per-attempt
  invocation, backoff, validation, and critical writes. A 100 ms recording margin
  and measured local 50 ms scheduler tolerance are not network or billing guarantees.
- Each invocation has a durable intent and separate outcome/usage record. Classified
  transient faults use full-jitter bounded backoff and Retry-After; a policy allows
  at most two retries per candidate and four total invocations. Permanent invalid
  requests terminate; credential failures do not retry the same provider. Selected
  transient 5xx statuses are configurable in the offline adapters. Extraction
  validation recovery is distinct and gets sanitized corrective guidance; refusal
  never triggers automatic evasion or successful unvalidated output.
- Redis atomically shares per-tenant/provider rate and concurrency admission
  across replicas. Provider-domain circuits open on five transient failures in
  60 seconds by default, cool down for 30 seconds, and allow one fenced half-open
  probe. Limits are configurable, lease expiry recovers lost ownership, and Redis
  outages fail closed. Readiness exposes sanitized mock state and failure count.
- Opt-in keyed operations have PostgreSQL ownership scoped by authenticated
  tenant/endpoint/key and a canonical caller fingerprint. A stable externally
  supplied 32-byte key HMACs identity and encrypts terminal replay with AES-GCM;
  the database does not retain plaintext output. Identical completed/failed
  requests replay under a new ingress ID with original evidence, conflicts are
  rejected, and concurrent owners cannot dispatch twice. Ownership lease expiry
  or disconnect marks unresolved work uncertain, preserves intent and conservative
  unknown-usage liability, and blocks automatic redispatch. The default logical
  replay lifetime is 24 hours; physical expired-content deletion is milestone 4.

The seeded v2 policy still permits one attempt. New immutable policies can opt
into bounded retry/fallback; the HTTP runtime still selects only the mock. This
is not an invoice guarantee or an exactly-once upstream claim.

## Evidence and fault matrix

| Fault or boundary | Evidence |
| --- | --- |
| 429, selected 5xx, timeout, connection, long Retry-After | Offline adapter/contract fixtures, scripted executor recovery, deadline/backoff tests |
| Permanent invalid request, credential failure, nontransient 5xx | No same-candidate retry, invalid-request terminal/no fallback, provider-domain credential exclusion |
| Malformed/schema-invalid/truncated/refused extraction | Bounded validation recovery, refusal stops, no unvalidated success, typed exhaustion |
| Deadline, attempt, spend ceiling | Fake-clock and real scheduler tests, four-call cap, no overlong sleep, server and caller ceilings, cheaper eligible fallback only within original ceiling |
| Circuit/open probe, rate/concurrency, outage | Two RedisControl instances, fenced stale probe report, expiring leases, API circuit gate, fail-closed Redis fault |
| Keyed duplicate, conflict, replay, expiry, tenant isolation | Unit/API and real PostgreSQL tests; AES-GCM tamper denial and separate ingress identity |
| Disconnect/crash and critical write | ASGI disconnect cancellation, stale-owner request/attempt/usage uncertainty, database-failure-before-dispatch and post-dispatch no-success tests |

Executed locally: `uv run ruff format --check app migrations tests deploy`,
`uv run ruff check app migrations tests deploy`, `uv run mypy`, `uv run pytest`,
`uv lock --check`, and strict OpenSpec validation. All passed; the final suite
had **213 passed** with real isolated PostgreSQL schemas and Redis. No test was
silently skipped. The local Lima/nerdctl image was rebuilt; the retained database
advanced through `0003_idempotency` and `0004_replay_recovery_index`; existing
credential reseeding, authenticated generate/extract smoke, and readiness passed.
The in-app browser loaded the refreshed OpenAPI description, showed the replay
and fallback response fields, executed readiness with HTTP 200 and healthy Redis,
and confirmed a synthetic unauthenticated generate call returns correlated 401.
The refreshed Swagger request example contains only the runnable synthetic input.
The private client key was not entered into the browser.

## Boundary and next gate

Live OpenAI/Anthropic dispatch remains disabled. Milestone 4 must add atomic
tenant/request budget reservations, unknown-usage holds/reconciliation, exact
cache, encrypted cache retention/invalidation, and integrated privacy/accounting
checks before any explicitly authorized paid smoke. Replay expiry prevents
serving old content, but ciphertext remains in storage until the later deletion
job; do not deploy with a retention promise that implies physical purging today.
Rotating the replay key inside the 24-hour window would break existing identity
and decryption; a managed rotation procedure is not yet implemented. No quality,
cost-saving, latency, staging, or load target is claimed as measured.
