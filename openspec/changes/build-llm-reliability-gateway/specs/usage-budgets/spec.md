## Purpose

Attribute provider usage to every attempt and bound admitted estimated spend
under concurrent execution, including failures and uncertain upstream billing.

## ADDED Requirements

### Requirement: Versioned decimal estimates and actual usage provenance

The gateway SHALL maintain immutable pricing versions and estimate input/output
cost using conservative token bounds and maximum output tokens. Observed token
usage and calculated estimated cost SHALL be distinguishable from provider
invoices. Every dispatched attempt, including failed/retried/fallback attempts,
SHALL have request/attempt IDs, tenant, model, pricing version, usage status,
and estimated or observed usage. Missing usage SHALL remain unknown, not zero.
Terminal responses and summaries SHALL aggregate all attempts, not only the
successful model. Monetary accounting SHALL use decimal arithmetic.

#### Scenario: Fallback succeeds after a billed failure

- **WHEN** the first attempt fails with observed usage and a second succeeds
- **THEN** both attempts contribute to request usage/cost with their own pricing provenance

#### Scenario: Provider reports no usage

- **WHEN** an upstream outcome lacks usage data
- **THEN** accounting labels it unknown and retains its conservative reservation until reconciliation

### Requirement: Atomic request and tenant spend admission

Before each provider dispatch, the gateway SHALL durably and atomically reserve
its conservative maximum estimated cost against the request ceiling and tenant
daily/monthly limits. Committed costs plus outstanding reservations SHALL be
considered across replicas. Retry, validation recovery, and fallback SHALL use
the same request ceiling; insufficient allowance SHALL prevent dispatch with
`BUDGET_EXCEEDED`. Daily/monthly boundaries SHALL use documented UTC periods.
Cached responses and idempotency replays SHALL add no new provider spend.
Cheap fallback SHALL be permitted only inside the original quality/spend limits,
not as a way to exceed an exhausted budget.

#### Scenario: Concurrent requests near a budget limit

- **WHEN** two requests cannot both fit the remaining tenant allowance
- **THEN** atomic admission grants at most the fitting reservations and rejected requests do not dispatch

#### Scenario: Retry exceeds the request ceiling

- **WHEN** existing cost/reservations plus the next attempt exceed `max_cost_usd`
- **THEN** no retry starts and the gateway returns `BUDGET_EXCEEDED`

### Requirement: Reconciliation and uncertain-execution protection

Successful or known-failed attempts SHALL reconcile observed usage once, release
only confirmed unused allowance, and preserve an auditable event history.
Timeouts/crashes after dispatch SHALL retain an uncertain reservation until a
documented recovery procedure establishes usage or applies a conservative charge.
A retry of reconciliation SHALL not double-count usage. Pricing/token-estimation
discrepancies SHALL be recorded and alertable; hard limits SHALL be described as
limits on admitted estimated liability, not guaranteed actual invoice ceilings.

#### Scenario: Worker crashes after provider dispatch

- **WHEN** a worker crashes before persisting a definitive outcome
- **THEN** allowance remains held and recovery does not assume the attempt was free or redispatch it automatically

#### Scenario: Duplicate usage event

- **WHEN** reconciliation processes the same attempt event twice
- **THEN** tenant/request usage is charged once and the evidence retains a stable event identity

### Requirement: Critical recording and tenant isolation

Durable request, dispatch-intent, reservation, terminal-state, and usage records
SHALL be correctness-critical. If this state cannot be written, no new provider
attempt SHALL start. If outcome recording fails after dispatch, execution SHALL
be treated as uncertain and no unrecorded success SHALL be claimed. Usage/budget
queries SHALL restrict access to authorized tenant/operator scope.

#### Scenario: Database unavailable before dispatch

- **WHEN** a dispatch intent or reservation cannot be committed
- **THEN** the gateway returns `DEPENDENCY_UNAVAILABLE` and performs no provider call

#### Scenario: Tenant requests another tenant's spend

- **WHEN** a tenant client requests spend records outside its authorized scope
- **THEN** access is denied without leaking totals or attempt details
