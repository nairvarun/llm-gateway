## Purpose

Choose provider/model candidates through an explainable versioned policy while
preserving capability constraints and a consistent provider-neutral contract.

## ADDED Requirements

### Requirement: Common provider capability and result contract

The gateway SHALL support a deterministic mock and at least two live providers
through one contract covering capabilities, invocation, normalized results,
usage availability, and classified failures. All adapters SHALL pass the same
contract suite. The mock SHALL support scripted timeout, 429, selected 5xx,
credential failure, malformed output, refusal, and missing-usage outcomes without
network calls or credentials.

#### Scenario: Provider usage is absent

- **WHEN** an adapter receives a result without token usage
- **THEN** it reports unavailable usage explicitly rather than inventing zero tokens

#### Scenario: Offline reliability testing

- **WHEN** the mock is configured with a repeatable fault sequence
- **THEN** identical invocations reproduce that sequence without paid calls

### Requirement: Versioned model and policy registry

The gateway SHALL resolve an immutable policy version and model-profile/pricing
snapshot for each accepted request. Profiles SHALL identify enabled state,
provider/model, context/output limits, capabilities, and pricing version. Policy
versions SHALL identify candidate order/weights, quality-tier eligibility,
tenant allowlists, fallback behavior, and limits. Existing requests SHALL retain
their snapshot when an operator publishes a new policy version.

#### Scenario: Policy changes during execution

- **WHEN** an authorized operator publishes a new version during an in-flight request
- **THEN** that request retains its original policy/pricing snapshot and later requests resolve the new version

### Requirement: Deterministic constrained candidate selection

The router SHALL filter candidates by task, resolved schema support, conservative
context/output estimates, tenant allowlist, enabled state, circuit health,
remaining deadline, and spend allowance before ranking. For the same request,
policy, registry, and captured health inputs, ordering and explanations SHALL be
identical, including stable tie-breaking. Quality and latency scores SHALL be
configured estimates, not guarantees of model correctness or response time.

#### Scenario: No eligible candidate

- **WHEN** every model violates at least one required constraint
- **THEN** the gateway returns `NO_ELIGIBLE_MODEL` with sanitized exclusion reasons and makes no provider call

#### Scenario: Equal ranking scores

- **WHEN** two candidates have identical ranking scores in the same snapshot
- **THEN** their order follows the documented stable tie-break rule

### Requirement: Explainable and authorized configuration changes

The gateway SHALL record selected policy/model versions, candidate order, and
non-sensitive inclusion/exclusion reasons under the request ID. Authorized
operators SHALL be able to publish/activate/roll back policy versions and disable
providers through a documented control path without deploying client code.
Mutations SHALL be validated and audited. Disabling a provider SHALL prevent new
attempts after the next configuration refresh, with a documented maximum refresh
delay; it SHALL NOT erase in-flight attempt evidence.

#### Scenario: Unauthorized policy mutation

- **WHEN** a tenant client without operator privileges tries to change routing configuration
- **THEN** the action is denied without changing the active configuration

#### Scenario: Provider disabled between attempts

- **WHEN** a provider is disabled and the refresh delay has elapsed before another attempt starts
- **THEN** no new attempt starts on that provider and the routing evidence records its exclusion
