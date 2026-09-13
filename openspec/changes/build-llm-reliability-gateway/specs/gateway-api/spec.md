## Purpose

Give application developers a stable tenant-authenticated contract for text
generation and schema-validated extraction, independent of provider APIs.

## ADDED Requirements

### Requirement: Authenticated and bounded requests

The gateway SHALL authenticate every `/v1` request, derive tenant identity from
the credential, enforce tenant authorization and configured payload/rate limits,
and reject invalid input before any provider invocation. Generation input SHALL
be text; streaming and multimodal requests SHALL be rejected in this version.

#### Scenario: Metadata cannot select another tenant

- **WHEN** an authenticated client places another tenant's ID in metadata
- **THEN** authorization and all state access use only the authenticated tenant

#### Scenario: Invalid or oversized input

- **WHEN** a request has invalid fields or exceeds configured payload limits
- **THEN** the gateway returns a typed 422 or 413 error without invoking a provider

#### Scenario: Revoked credential or exhausted rate allowance

- **WHEN** a client uses a revoked credential or exceeds its request-rate allowance
- **THEN** the gateway returns 401 or 429 respectively without invoking a provider

### Requirement: Versioned generation contract

`POST /v1/generate` SHALL accept `input`, `model_policy`, `quality_tier`,
`latency_budget_ms`, `max_cost_usd`, `temperature`, `max_output_tokens`,
`cache_mode`, bounded `metadata`, and optional `idempotency_key`. Optional
`task_type` SHALL select a supported task class; omission SHALL mean generation.
The API SHALL publish types, defaults, ranges, supported enums, and error schemas
in OpenAPI. Successful responses SHALL contain `request_id`, text `output`,
`provider`, `model`, `finish_reason`, `usage`, `estimated_cost_usd`,
`latency_ms`, `cache_hit`, `idempotency_replayed`, `fallback_used`, and
`policy_version`. Monetary values SHALL use nonnegative decimal USD values,
not floating-point accounting. Provider-specific wire types SHALL not appear in
the public contract.

#### Scenario: Equivalent requests through different adapters

- **WHEN** two supported providers successfully answer the same API request
- **THEN** both responses use the same field types and normalized finish reasons

#### Scenario: Unsupported mode

- **WHEN** a client requests streaming or an unknown quality/cache/task value
- **THEN** the gateway returns `INVALID_REQUEST` without upstream work

### Requirement: Validated extraction contract

`POST /v1/extract` SHALL accept the same routing, spend, deadline, and idempotency
constraints plus exactly one inline JSON Schema or registered schema name/version.
The gateway SHALL validate the schema against its documented supported dialect
and limits, reject remote references, and return parsed JSON only after successful
validation. Truncated, refused, malformed, or schema-invalid output SHALL NOT
be returned as extraction success. Output-validation retry/fallback SHALL consume
the same deadline, attempt, and spend limits as all other attempts.

#### Scenario: Invalid schema

- **WHEN** a schema is unsupported, too complex, or contains a remote reference
- **THEN** the gateway returns `INVALID_SCHEMA` before invoking a provider

#### Scenario: No valid extraction before limits are exhausted

- **WHEN** provider output remains malformed or schema-invalid until no eligible attempt remains
- **THEN** the gateway returns `OUTPUT_VALIDATION_FAILED` unless a deadline, spend, or critical-dependency limit caused exhaustion, in which case the corresponding typed error takes precedence

#### Scenario: Cache does not bypass validation

- **WHEN** an extraction cache entry fails validation against the resolved schema
- **THEN** the entry is treated as unusable and never returned as a successful extraction

### Requirement: Stable error and correlation contract

Errors SHALL contain `error.code`, a sanitized `error.message`,
`error.retryable`, and `request_id`. The published contract SHALL map auth to
401/403, request/schema validation to 422, size to 413, conflict to 409,
rate/spend exhaustion to 429, unavailable dependencies/candidates to 503,
exhausted invalid output to 502, and overall deadline expiry to 504.
`retryable` SHALL describe safe client retry of the gateway operation, not just
the upstream error class; ambiguous execution without an idempotency key SHALL
NOT be advertised as safe to retry. Every response SHALL expose a request ID
usable in operator queries without leaking credentials or raw provider errors.

#### Scenario: Provider rejects its credential

- **WHEN** provider credential failure leaves no eligible alternative
- **THEN** the client receives sanitized `PROVIDER_UNAVAILABLE` metadata rather than the provider key, wire error, or a client-authentication 401

### Requirement: Dependency-aware health endpoints

`GET /health/live` SHALL report process liveness without probing dependencies.
`GET /health/ready` SHALL return 503 if critical durable state or configured
request admission controls are unavailable; optional cache/telemetry degradation
SHALL be reported without failing readiness. A live-provider outage SHALL be
visible as degradation and SHALL NOT alone imply process failure. Health
responses SHALL NOT expose credentials, internal addresses, or tenant data.

#### Scenario: Optional exporter failure

- **WHEN** the telemetry exporter fails but critical dependencies are healthy
- **THEN** readiness succeeds and reports sanitized telemetry degradation

#### Scenario: Durable state failure

- **WHEN** durable usage, reservation, or idempotency state is unavailable
- **THEN** readiness returns 503 and new provider execution fails closed
