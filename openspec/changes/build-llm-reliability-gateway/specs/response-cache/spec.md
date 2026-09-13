## Purpose

Reuse eligible exact responses within a tenant while making expiration,
invalidation, privacy, and current-request usage distinct from original execution.

## ADDED Requirements

### Requirement: Explicit exact-cache eligibility and identity

The baseline cache SHALL use exact canonical request identity, not embedding
similarity. Reads/writes SHALL require policy eligibility and client mode;
omission SHALL mean bypass. Eligible requests SHALL use deterministic settings,
an approved privacy classification, and bounded TTL. Identity SHALL include
authenticated tenant/application namespace, endpoint/task, canonical input,
resolved schema, policy/model-registry version, all output-affecting parameters,
and application cache version. Canonicalization SHALL NOT discard significant
whitespace or conflate distinct inputs. Semantic reuse SHALL not be silently
enabled in this capability.

#### Scenario: Different schemas or tenant namespaces

- **WHEN** otherwise identical requests use different schemas or authenticated tenants
- **THEN** they do not share a cache entry

#### Scenario: Sensitive or nondeterministic request

- **WHEN** input classification or generation parameters violate eligibility
- **THEN** the cache is bypassed even if the client requested caching

### Requirement: Expiration and validated cache responses

The cache SHALL store protected response content, source request/provider/model,
policy versions, creation time, expiry, and original usage provenance. Reads
SHALL reject expired/corrupt entries and entries whose model is now disabled or
whose current request constraints are no longer satisfied. Extraction hits SHALL
be validated against the resolved schema. A hit SHALL expose `cache_hit: true`,
current ingress latency/request ID, zero new provider tokens/cost, and separately
labeled source usage; it SHALL NOT count source tokens as fresh billable work.

#### Scenario: Expired entry

- **WHEN** an entry is at or beyond its expiry time
- **THEN** the request behaves as a miss and the expired output is not returned

#### Scenario: Valid hit

- **WHEN** an eligible unexpired entry satisfies the current request constraints
- **THEN** no provider is invoked and usage distinguishes current zero spend from source execution

### Requirement: Authorized invalidation and bounded single-flight

Authorized operators SHALL be able to invalidate an exact entry or
advance a tenant/application namespace version through a documented control
path. An invalidation acknowledged as complete SHALL prevent later reads/writes
from making old-generation entries visible. Concurrent cache misses for the
same identity SHALL use bounded cross-replica single-flight ownership; waiters
SHALL remain within their deadlines. Loss of lock ownership SHALL prevent stale
owners from publishing results. Cache single-flight SHALL not be advertised as
exactly-once provider execution.

#### Scenario: Invalidation races with a slow writer

- **WHEN** a namespace advances while an old-generation provider request is in flight
- **THEN** its eventual cache write cannot repopulate the active namespace

#### Scenario: Concurrent healthy misses

- **WHEN** identical eligible misses arrive while a valid single-flight owner exists
- **THEN** only the owner invokes the provider and waiters consume its validated result or stop at their own deadline

### Requirement: Optional-cache failure behavior

Cache read/write/lock failure SHALL allow uncached execution only if critical
idempotency, budget, rate, and concurrency controls remain healthy. Cache
degradation SHALL be recorded and never silently bypass those controls.

#### Scenario: Cache unavailable but admission controls healthy

- **WHEN** optional cache storage fails and all critical controls are available
- **THEN** execution proceeds uncached and reports cache degradation without returning a stale entry
