## Purpose

Provide a reproducible, reviewable path from local development to staging with
secret protection, tenant access boundaries, operational evidence, and rollback.

## ADDED Requirements

### Requirement: Offline reproducible local environment

A clean checkout SHALL provide documented startup and test commands with
containerized dependencies and a deterministic mock provider. Default local
startup and CI SHALL require no paid account, provider key, or cloud resources.
Migration/configuration errors SHALL fail with actionable sanitized messages.
Fixtures/datasets SHALL be synthetic or explicitly approved and sanitized.

#### Scenario: Fresh local checkout without credentials

- **WHEN** a developer follows the documented quickstart without provider/AWS keys
- **THEN** the service starts in mock mode and generation, extraction, failure injection, and an evaluation demo can be exercised offline

### Requirement: Automated quality and compatibility checks

Pull requests SHALL run format/lint/type checks, unit/API/adapter-contract tests,
dependency/secret scans, OpenAPI compatibility checks, and applicable integration
and fault tests. Prompt/policy/model/evaluator changes SHALL run applicable
evaluation gates. CI SHALL not silently skip required checks because live
credentials are absent; offline fixtures SHALL provide the default evidence.

#### Scenario: Gate lacks required evidence

- **WHEN** a required test or evaluation gate has no evidence
- **THEN** the check fails or blocks promotion rather than being reported as passed

### Requirement: Secret handling and untrusted-content boundaries

Client credentials SHALL be stored as verification hashes with rotation and
revocation. Provider secrets SHALL be supplied by external secret storage and
never included in source, images, logs, datasets, or CI artifacts. Transport and
protected storage SHALL be encrypted. Model-generated content SHALL not be
executed or used to fetch arbitrary URLs. Inline schemas SHALL not trigger
network resolution. Operator/configuration privileges SHALL be distinct from
tenant execution privileges, with audited changes.

#### Scenario: Adversarial model output includes instructions or a URL

- **WHEN** a provider emits executable code, control instructions, or a URL
- **THEN** the gateway treats it only as untrusted response data and performs no execution or automatic fetch

### Requirement: Retention and protected artifact access

Raw production prompts/outputs SHALL not be durably retained by default, except
short-lived encrypted result content explicitly required for opted-in caching
or keyed idempotency replay. Retention SHALL be documented and configurable for
those stores, metadata, traces, and evaluation artifacts. Debug/online sampling
SHALL require explicit policy opt-in, redaction controls, and bounded retention;
redaction limitations SHALL be documented. Artifact access and deletion SHALL
respect authenticated tenant scope and privileged audit requirements.

#### Scenario: Protected result retention expires

- **WHEN** a cache or idempotency result reaches its content-retention limit
- **THEN** protected content is deleted or made unreadable and the documented replay/expiry semantics apply without serving expired personal data

### Requirement: Reviewed EKS staging infrastructure and rollback

Infrastructure SHALL be reproducible from documented Terraform/configuration,
with a reviewed plan, least-privilege roles, private data services, external
secrets, bounded log/artifact retention, and environment-specific spend controls.
The staging runtime SHALL use AWS EKS with private worker nodes, Kubernetes
service accounts mapped to least-privilege AWS permissions, namespace-scoped
workloads, health probes, resource requests/limits, disruption-safe rollout
settings, and encrypted ingress. Database migrations SHALL run as an explicit
bounded Kubernetes Job before API promotion; application pods SHALL NOT run
migrations concurrently. Staging promotion SHALL require immutable image identity,
migration checks, smoke tests, evaluation/security evidence, and a documented
Kubernetes rollout rollback exercise.
Cloud provisioning/live-provider execution SHALL require explicit owner
authorization and SHALL not be an automatic consequence of a local test.

#### Scenario: Staging rollout fails smoke tests

- **WHEN** a new image or policy fails the required staging checks
- **THEN** promotion is blocked and the documented prior image/policy recovery is exercised without destructive schema rollback

#### Scenario: Kubernetes workload lacks required identity or secrets

- **WHEN** a gateway pod cannot assume its approved service-account identity or retrieve its required external secrets
- **THEN** the pod fails readiness and no fallback node credential, embedded secret, or broadened IAM permission is used
