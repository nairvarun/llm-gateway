## Purpose

Bound request execution under provider failures and concurrency while making
retry, fallback, cancellation, and duplicate-request behavior observable.

## ADDED Requirements

### Requirement: One deadline and bounded attempts

The gateway SHALL propagate one overall request deadline through admission,
locking, cache access, provider calls, backoff, validation, and critical writes.
Per-attempt timeouts SHALL not exceed remaining time. Defaults SHALL permit at
most two retries per candidate and four total provider invocations across all
candidates; policies SHALL publish their effective bounds. No attempt or sleep
SHALL start if it cannot fit remaining time or violates attempt/spend limits.
Cancellation SHALL stop further attempts but SHALL NOT imply upstream work was
unbilled. Deadline enforcement SHALL be measured with a published local scheduler
tolerance, not a promise of exact network response timing.

#### Scenario: Retry would outlive the deadline

- **WHEN** the remaining deadline cannot accommodate the next backoff/attempt
- **THEN** no new attempt starts and the gateway returns `DEADLINE_EXCEEDED`

#### Scenario: Client disconnects after dispatch

- **WHEN** a client disconnects during provider execution
- **THEN** the gateway initiates cancellation, starts no further attempts, and retains uncertain usage evidence for reconciliation

### Requirement: Classified retry and constraint-preserving fallback

Automatic upstream retries SHALL be limited to timeouts, connection failures,
429, and configured transient 5xx, using bounded exponential backoff with jitter
and respecting `Retry-After` only within the remaining deadline. Provider
credential and invalid-request failures SHALL NOT be retried on the same
candidate; credential failure SHALL allow another eligible provider, while an
invalid upstream request SHALL terminate the operation. Validation recovery
SHALL be separately classified and use the same total bounds. Fallback SHALL
never relax caller schema, quality, tenant, deadline, or spend constraints.

#### Scenario: Rate limit includes a long Retry-After

- **WHEN** a provider returns 429 with Retry-After longer than remaining time
- **THEN** the gateway does not sleep past the deadline and tries another eligible candidate only if all limits permit

#### Scenario: Invalid provider request

- **WHEN** a provider reports a permanent invalid-request error
- **THEN** the gateway makes no retry/fallback for that error and returns a sanitized typed failure

### Requirement: Circuit and concurrency isolation

The gateway SHALL expose closed/open/half-open circuit behavior and bounded
half-open probes per configured provider failure domain. Counts/thresholds and
cooldown SHALL be configurable and observable. Per-tenant and per-provider
concurrency admission SHALL be bounded across deployed replicas, with waiting
time included in the deadline. Rate and concurrency control failures SHALL reject
new execution rather than silently admitting unlimited work.

#### Scenario: Provider circuit is open

- **WHEN** a request would select a provider with an open circuit
- **THEN** no normal attempt is dispatched there until an allowed half-open probe succeeds

#### Scenario: No capacity before deadline

- **WHEN** tenant/provider capacity cannot be acquired within the deadline
- **THEN** the request fails without exceeding configured concurrency

### Requirement: Tenant-scoped idempotency state

Idempotency SHALL be scoped by authenticated tenant, endpoint, and key, with a
fingerprint of all caller output-affecting fields and constraints. Records SHALL
publish retention and use in-progress, completed, failed, and uncertain states.
A completed identical request within retention SHALL replay its protected
terminal result without new upstream work; a changed fingerprint SHALL return
`IDEMPOTENCY_CONFLICT`. Concurrent identical requests SHALL NOT dispatch duplicate
attempt sequences; a duplicate SHALL receive `REQUEST_IN_PROGRESS` with retry
guidance. Replays SHALL be explicitly identified and retain original execution
evidence while recording the new ingress request ID separately.

#### Scenario: Same key with changed input

- **WHEN** a tenant reuses an unexpired key with a different fingerprint
- **THEN** the gateway returns 409 `IDEMPOTENCY_CONFLICT` without provider execution

#### Scenario: Identical concurrent requests

- **WHEN** two identical same-tenant requests arrive with the same unexpired key
- **THEN** only one owns execution and the other receives 409 `REQUEST_IN_PROGRESS`

#### Scenario: Worker crashes after dispatch

- **WHEN** a worker disappears with unresolved upstream execution
- **THEN** the key becomes uncertain and duplicates return `EXECUTION_UNCERTAIN` without redispatch until reconciliation resolves it

#### Scenario: Another tenant uses the same key

- **WHEN** a second authenticated tenant submits the same literal idempotency key
- **THEN** no result or execution state from the first tenant is read or replayed
