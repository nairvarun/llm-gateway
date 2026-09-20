# Milestone 4 verification — offline budgets and exact cache

Verified locally on 20 September 2026 against synthetic mock traffic. OpenSpec
tasks 4.1–4.9 have implementation and integrated verification evidence. The
full program remains active. No paid provider, cloud resource, quality benchmark,
or production SLO was used or measured.

## Delivered behavior

- PostgreSQL owns UTC day/month buckets, a persisted per-request ceiling, and one
  reservation per attempt. Admission locks bucket rows transactionally across
  replicas; known usage releases only confirmed unused allowance. Unknown usage
  retains the original-period hold, including after a crash. Duplicate settlement
  is idempotent. An operator can apply an audited conservative charge. A measured
  overrun is recorded and disables the provider for later attempts. These are
  limits on admitted estimated liability, not guarantees about an invoice.
- Dispatch intent and reservation commit together before an invocation. A
  failed insert prevents dispatch. Attempt/usage reconciliation commits together;
  a post-dispatch write failure cannot return an unrecorded success.
- Exact caching is disabled by default. An authenticated request must opt in,
  attest approved non-sensitive content, use temperature zero, and belong to an
  operator-approved tenant. HMAC identity includes tenant/application, endpoint,
  exact input bytes through canonical JSON, resolved schema, all caller fields
  affecting eligibility/output, and pinned policy/model/pricing identities.
  Whitespace is not normalized. Cache entries hold encrypted result/provenance
  with a one-hour maximum TTL; hits recheck current model eligibility and local
  extraction validation, then record a new ingress with zero fresh provider
  usage/cost and a source request ID.
- Redis owns optional cache entries and fenced single-flight leases. PostgreSQL
  owns audited exact-key and namespace generations; an old slow writer cannot
  populate a new generation. Cache-only faults degrade to uncached execution
  only if critical PostgreSQL and Redis admission controls remain healthy.
- An authenticated `/v1/spend` view and operator CLI expose current UTC
  allowances without cross-tenant access. A bounded operator purge deletes
  expired replay ciphertext and old operational metadata while retaining
  unresolved holds and the active month bucket. Redis TTL deletes cache entries.
  Credentials, immutable configuration, and invalidation generations have
  separate lifecycles; backup/snapshot erasure is not configured here.

The seeded mock policy still allows one attempt and costs zero. Tests use
nonzero synthetic prices and scripted failures to exercise accounting. Live
OpenAI/Anthropic dispatch remains hard-gated despite the completed offline gate;
any paid smoke requires a separate explicit request and recorded spend ceiling.

## Evidence matrix

| Boundary | Offline evidence |
| --- | --- |
| Competing UTC daily/monthly admissions and request ceiling | Two PostgreSQL sessions, near-exhausted limits, known-cost release, duplicate settlement, day/month rollover |
| Missing usage and crash recovery | Held reservation, stale dispatched attempt, one audited conservative charge, no free-work assumption |
| Overrun and critical write failure | Overrun row/provider disable, failed reservation insert before dispatch, failed usage insert after dispatch without success |
| Exact key/eligibility | Whitespace, schema, tenant/application, parameter and version isolation; bypass/read-only/read-write; sensitive/nondeterministic bypass |
| Protected cache hit | AES-GCM envelope, Redis TTL/logical expiry/corruption miss, disabled model/extraction recheck, zero fresh tokens/cost and source ID |
| Single-flight and invalidation | Repeated concurrent requests, stale lease token rejection, slow-writer namespace race, deadline-bounded waiter, audited CLI generations |
| Scope and optional failure | Cross-tenant spend denial, tenant-only HTTP view, optional cache degradation with healthy controls, fail-closed critical controls |
| Retention and privacy | Expired replay deletion, old metadata deletion with unknown hold preserved, encrypted entry contains no raw synthetic input/output/key |

Executed locally: Ruff format/lint, mypy, the full pytest suite with isolated
real PostgreSQL schemas and Redis namespaces, `uv lock --check`, and strict
OpenSpec validation. All passed; **241 tests passed** after the final API example
fix. No required test was skipped. The local Lima/nerdctl image rebuilt and
advanced the retained database through `0005_budget_reservations` and
`0006_cache_generations`. Existing local credentials were verified without
replacement; authenticated generate/extract smoke, a scoped spend-summary CLI
query, and readiness with healthy PostgreSQL/Redis passed. The browser recheck
confirmed the Milestones 1–4 OpenAPI boundary, sensible USD response examples,
and an unauthenticated `/v1/spend` request returning a typed 401 with a request
ID. Browser testing did not enter a client key or invoke a paid provider.

## Residual boundaries

Caller cache classification is an attestation, not automated sensitive-data
detection; an operator must approve only trusted applications and content. Key
rotation across live cache/replay retention windows is not yet managed. The
retention CLI must be scheduled by an operator; local Docker/Lima does not
configure production backup expiry or secure deletion. Shared Redis admission
remains correctness-critical; a cache-only outage is optional. Live-provider
strict token bounds remain unavailable, so no paid adapter can dispatch through
this API. Evaluation, telemetry, benchmark evidence, and staging are later gates.
