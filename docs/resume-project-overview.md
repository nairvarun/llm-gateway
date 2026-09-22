# LLM Reliability Gateway — resume project dossier

Prepared 22 September 2026 for use by a resume-optimization agent.

Repository: <https://github.com/gultandon/Llm-gateway>

## Claim-status legend

This dossier deliberately separates verified engineering work from planned or
conditional claims. A resume must not collapse these categories.

- **Verified now:** Implemented in the repository and exercised by local or CI
  checks.
- **Valid only after the reviewed Terraform apply succeeds:** Infrastructure
  foundation that may be described as provisioned only after AWS reports the
  successful creates and the resulting resources are inspected.
- **Not yet valid after the current apply:** Runtime deployment, live-provider
  execution, production traffic, production SLOs, and cloud load/restore evidence.

The currently reviewed Terraform plan creates an EKS staging foundation, but no
apply has occurred. Kubernetes workload manifests exist separately and require
an immutable ECR digest plus externally populated secrets. Applying Terraform
alone does **not** submit the migration Job, API Deployment, Service, disruption
budget, or evaluation Job. Image build/push, secret population, workload rollout,
and cluster verification are required before claiming that the API runs on AWS.

## Executive overview

The LLM Reliability Gateway is a greenfield, provider-neutral Python service
designed to sit between applications and large-language-model providers. It
turns direct model calls—which commonly have inconsistent response shapes,
unbounded retry behavior, invalid structured output, duplicate execution risk,
and weak spend attribution—into an authenticated and auditable execution path.

The project provides generation and schema-validated extraction APIs, a common
provider contract, deterministic model routing, bounded retry and fallback,
shared circuit breakers and admission controls, durable idempotency, encrypted
exact caching, atomic spend reservations, reproducible evaluation, and
privacy-conscious operational evidence. It uses PostgreSQL for durable
correctness state and Redis for distributed ephemeral coordination.

The project was developed through five completed offline milestones and one
gated AWS staging milestone. The offline HTTP runtime intentionally remains
mock-only. OpenAI and Anthropic adapters exist and pass the same sanitized,
fixture-backed contract suite, but paid network dispatch is disabled. This
boundary is an explicit safety decision, not missing error handling.

## Personal scope and ownership

The work covers the complete lifecycle of a backend/platform project:

- Translated an informal handoff into a structured OpenSpec program with seven
  capability areas, implementation decisions, scenarios, milestone gates, and
  explicit non-goals.
- Designed the service and persistence architecture from a greenfield starting
  point.
- Implemented the FastAPI application, provider abstractions, routing engine,
  reliability controls, security boundaries, persistence layer, evaluation
  tooling, observability, command-line administration, migrations, tests, local
  containers, CI, and Terraform.
- Built failure-oriented verification instead of testing only success paths.
- Preserved evidence for claims through verification reports, synthetic
  benchmark output, CI results, and account-specific Terraform plans.
- Kept provider spending and cloud provisioning behind explicit authorization
  gates.

The repository currently contains roughly 14,000 lines across the application,
tests, migrations, deployment scripts, and Terraform. This is a codebase-size
description, not a productivity or business-impact metric.

## Architecture and technology stack

### Application architecture

- Python 3.12 asynchronous application.
- FastAPI and Pydantic for HTTP routing, validation, OpenAPI, settings, and typed
  public contracts.
- A single service with explicit internal boundaries rather than premature
  microservices.
- Provider-neutral domain models keep OpenAI, Anthropic, HTTP, and database
  implementation types out of the public API contract.
- Async SQLAlchemy and asyncpg for PostgreSQL persistence.
- Redis for cross-replica admission, circuits, leases, cache coordination, and
  optional encrypted cache entries.
- Alembic for nine forward database migrations.
- JSON Schema draft 2020-12 validation with a deliberately bounded local subset.
- AES-GCM for protected replay and cache payloads; keyed hashes/HMAC identities
  for protected lookup and canonical request identity.
- OpenTelemetry spans, Prometheus-compatible metrics, and sanitized structured
  operational events.

### Provider integrations

- Deterministic scripted mock provider for offline development, fault injection,
  CI, evaluation, and demonstrations.
- OpenAI adapter using the pinned `openai==3.16.2` SDK.
- Anthropic adapter using the pinned `anthropic==1.7.0` SDK.
- Selected offline registry entries include OpenAI `gpt-5.6-luna` and Anthropic
  `claude-haiku-4-5-20251001`.
- Provider SDK automatic retries are disabled so every retry and fallback stays
  visible to the gateway's deadline, attempt, and spend controls.
- Both vendor adapters use hand-authored sanitized fixtures and in-memory
  transports in tests. They have not sent live vendor requests.

### Data and infrastructure stack

- PostgreSQL 16 for authoritative durable state.
- Redis 7 for shared correctness controls and optional exact-cache storage.
- Docker-compatible image and Compose-based local environment; locally exercised
  with Lima/nerdctl and exercised in GitHub Actions with Docker Compose.
- Non-root runtime container using UID 10001.
- uv for dependency locking and reproducible Python environments.
- Terraform 1.16.3 and AWS provider 6.65.0 for the reviewed AWS staging design.
- GitHub Actions for quality checks, integration tests, container smoke,
  dependency audit, secret scanning, OpenSpec validation, benchmark execution,
  and Terraform static validation.

## Formal planning and requirements work

The original handoff was reorganized into an OpenSpec program with seven
capabilities:

1. `gateway-api`: authentication, generation/extraction contracts, schema
   limits, typed errors, and health behavior.
2. `provider-routing`: provider abstraction, immutable registries, deterministic
   ranking, policy versioning, and operator controls.
3. `request-reliability`: shared deadlines, retries, fallback, circuits,
   admission, idempotency, and uncertain execution.
4. `response-cache`: opt-in exact caching, version isolation, expiry,
   invalidation, and stampede protection.
5. `usage-budgets`: immutable prices, atomic reservations, reconciliation, and
   durable attempt-level accounting.
6. `evaluation-observability`: datasets, scorers, gates, telemetry, summaries,
   alerts, and retention-aware evidence.
7. `deployment-security`: reproducible local/CI/staging environments, secrets,
   access boundaries, auditing, rollback, and threat-model requirements.

The design explicitly excludes a chatbot UI, general agent/RAG framework,
training, streaming, every provider/modality, adaptive routing, semantic cache,
multi-region deployment, and Kubernetes portability/operator development from the baseline. These exclusions
keep correctness and failure behavior inspectable.

## API and request contract

### Implemented HTTP surface

- `POST /v1/generate`: authenticated provider-neutral text generation.
- `POST /v1/extract`: authenticated extraction whose successful result must
  pass local JSON Schema validation.
- `GET /v1/spend`: tenant-scoped current UTC daily and monthly committed, held,
  and remaining allowance.
- `POST /v1/evaluations/runs`: enqueue a version-pinned evaluation run and
  return HTTP 202.
- `GET /v1/evaluations/runs/{run_id}`: read an authorized evaluation run.
- `GET /v1/evaluations/runs/{run_id}/gate`: calculate the configured promotion
  gate with explicit missing-evidence blockers.
- `GET /v1/metrics/summary`: tenant/application-scoped summaries for bounded
  time windows and distinct application/evaluation traffic.
- `GET /metrics`: operator-only Prometheus-compatible metrics.
- `GET /health/live`: process liveness independent of downstream health.
- `GET /health/ready`: dependency-aware readiness that reports sanitized
  PostgreSQL, Redis, cache, circuit, provider, and telemetry state.

### Boundary validation

- Maximum HTTP body: 256 KiB.
- Maximum input: 100,000 characters.
- Maximum inline schema: 32 KiB.
- Maximum resolved schema depth: 16.
- Default output bound: 512 tokens; public maximum: 16,384 tokens.
- Default request deadline: 30 seconds; allowed range: 100 ms to 120 seconds.
- Metadata is bounded to short string pairs rather than arbitrary structures.
- Invalid JSON, oversized bodies, unsupported modes, schema conflicts, invalid
  fields, and invalid schemas are rejected before provider invocation.
- JSON Schema remote references, cycles, unsupported unsafe constructs, excessive
  depth, duplicate JSON keys, non-finite numbers, and overflowing numeric values
  are rejected or bounded.
- Refused, truncated, malformed, or schema-invalid extraction output never
  returns as a successful extraction.

### Typed error model

The service maps failure classes to stable public codes and HTTP statuses,
including authentication/authorization failures, invalid request/schema,
payload limits, rate and budget exhaustion, idempotency conflicts, in-progress
or uncertain execution, dependency failures, provider exhaustion, output
validation failure, and deadline exhaustion. Provider-specific error objects do
not leak through the API.

## Authentication, authorization, and privacy

- Client API keys are stored as hashes; plaintext keys are generated into local
  private files for development and are never embedded in images or fixtures.
- Authentication derives tenant, application, and role from the credential.
  Request metadata cannot select another tenant.
- Tenant, application, operator, and cross-tenant boundaries are enforced in
  the API, evidence queries, schema lookup, spend queries, evaluation records,
  cache administration, metrics, and configuration controls.
- Credentials can be revoked through an audited operator path.
- The default durable request/attempt records do not store raw prompts or model
  outputs.
- Default logs and metrics avoid prompts, output, API keys, arbitrary metadata,
  and unbounded tenant/request labels.
- Protected replay and cache content uses AES-GCM envelopes and stable external
  secrets; ciphertext tampering is rejected.
- HMAC-based identities prevent storing plaintext idempotency/cache keys.
- Secret scanning runs in CI through gitleaks.
- Provider responses and model output are treated as untrusted input.
- Generated content is never executed as code, instructions, or URLs.

## Durable state and database model

Nine Alembic migrations create and evolve 19 durable tables covering:

- Tenants and hashed credentials.
- Immutable policy, model, pricing, and other configuration versions.
- Immutable tenant-scoped named schema versions.
- Requests, individual attempts, usage events, dispatch evidence, terminal
  outcomes, and audit events.
- Active routing/provider controls and captured routing explanations.
- Idempotency ownership and distinct ingress/replay records.
- UTC daily/monthly budget buckets and per-attempt spend reservations.
- Cache namespace and exact-key invalidation generations.
- Versioned evaluation datasets, runs, cases, and approved baselines.

Important transactional guarantees include:

- Dispatch intent is persisted before sending work to a provider.
- Spend reservation and dispatch intent commit together.
- Attempt outcome and usage reconciliation commit together.
- A pre-dispatch persistence failure prevents invocation.
- A post-dispatch persistence failure cannot return an unrecorded success.
- Unique event and attempt identities make duplicate settlement idempotent.
- Every request and attempt captures immutable policy/model/pricing identity and
  sanitized routing evidence.

## Deterministic provider routing

- Registry, policy, model, and pricing versions are immutable PostgreSQL records.
- An audited pointer identifies the active policy without rewriting history.
- Candidate filtering checks task and schema capability, context/output limits,
  tenant restrictions, provider disablement, captured health, remaining
  deadline, and spend allowance.
- Eligible candidates are ranked by a fixed-bound weighted score combining
  configured quality, affordability, expected latency, and health.
- Stable provider/model identifiers break ties, so identical snapshots produce
  identical orderings.
- Raw score inputs, weights, exclusions, and selected identities are captured
  as explainable routing evidence.
- Provider enablement is checked again immediately before dispatch, closing the
  gap between selection and execution.
- Operator commands publish validated immutable configuration, activate or
  roll back policies, and enable or disable providers with audit records.
- Live profiles whose conservative token/cost upper bounds are unavailable fail
  closed for strict-budget execution instead of silently risking overspend.

## Reliability and failure handling

### Deadline and retry model

- One monotonic deadline begins at gateway receipt and covers state reads,
  admission, cache, routing, invocation, backoff, validation, and critical
  writes.
- The system reserves a 100 ms terminal-recording margin.
- Per-attempt timeouts and backoff consume the same remaining deadline.
- Default retry policy permits at most two retries per candidate and four total
  invocations.
- Backoff uses injectable full jitter, starts at 100 ms, caps at two seconds,
  and respects bounded Retry-After values.
- Retry eligibility is based on normalized classifications: timeouts,
  connection faults, 429s, and selected transient 5xx errors can recover;
  permanent invalid requests do not retry; credential failures do not retry the
  same provider.
- Schema-validation recovery is separate from transport retry and uses sanitized
  corrective guidance. Refusal is not automatically bypassed through another
  provider.
- Error precedence is deterministic when deadline, spend, dependency, and
  validation failures overlap.

### Redis-shared controls

- Tenant and provider rate/concurrency admission is atomic across replicas.
- Default tenant concurrency is 8 and provider concurrency is 16.
- Default tenant rate is 60 requests/minute and provider rate is 300/minute.
- Concurrency leases expire so abandoned ownership can recover.
- Provider-domain circuits open after five transient failures in a 60-second
  window, cool down for 30 seconds, and permit one fenced half-open probe.
- Fencing prevents a stale probe owner from closing a newer circuit state.
- PostgreSQL failures before dispatch and Redis admission/control failures fail
  closed. An optional cache-only failure may degrade to uncached execution only
  while critical admission and budget controls remain healthy.

### Disconnects and uncertain execution

- ASGI disconnects cancel local work within the bounded execution path.
- A timeout or disconnect never proves that an upstream provider stopped work or
  will not bill it.
- If ownership expires after dispatch without authoritative outcome evidence,
  the operation becomes uncertain, retains dispatch and potential-liability
  evidence, and is not automatically redispatched.
- The project intentionally does not claim exactly-once external execution.

## Durable idempotency and encrypted replay

- Idempotency is opt-in and scoped by authenticated tenant, application,
  endpoint, and client key.
- A canonical fingerprint includes all caller fields, resolved defaults,
  resolved schema content, constraints, and the initial immutable policy/pricing
  snapshot.
- Concurrent identical callers compete for one PostgreSQL-backed owner; only
  the owner may dispatch.
- Completed and failed terminal results can replay through a new ingress request
  while retaining the original execution/request identity.
- A changed request under the same key returns an idempotency conflict.
- Protected replay content is encrypted with AES-GCM and expires logically after
  24 hours by default.
- Expired or uncertain execution is never treated as proof that a fresh dispatch
  is safe.
- A bounded retention operation physically deletes expired replay content and
  old eligible metadata while preserving unresolved financial liability.

## Atomic spend accounting

- PostgreSQL owns tenant UTC daily and monthly buckets, per-request ceilings,
  per-attempt reservations, committed amounts, and outstanding holds.
- Admission transactionally locks budget rows, preventing two replicas from
  over-admitting a nearly exhausted allowance.
- Every retry and fallback consumes the same original request ceiling rather
  than resetting available spend.
- Money and estimates use decimal arithmetic and immutable pricing versions.
- Known usage settles exactly once and releases only the confirmed unused
  reservation.
- Missing or uncertain usage retains the conservative bound rather than being
  treated as free work.
- An operator can record an audited conservative charge during recovery.
- Period rollover preserves liability in the period where the attempt began.
- Reported usage that exceeds the bound is recorded as an overrun and disables
  unsafe later attempts.
- The `/v1/spend` API and `spend-summary` CLI expose committed, held, and
  remaining allowance within authorization boundaries.
- The system describes these controls as bounded estimated liability—not as a
  guarantee about a provider's changing invoice.

## Opt-in encrypted exact cache

- Cache is disabled by default.
- Eligibility requires explicit `read_only` or `read_write` mode, temperature
  zero, caller attestation that content is approved and non-sensitive, and
  operator approval for the tenant/application.
- Cache identity includes tenant/application, endpoint, exact request bytes,
  schema, parameters, immutable policy/model/pricing versions, and invalidation
  generation.
- Significant whitespace remains part of identity. The project does not claim
  semantic similarity or rewrite prompt text.
- Cache entries contain encrypted result and provenance envelopes.
- Default maximum TTL is one hour.
- Hits recheck current model eligibility and revalidate extraction output before
  returning it.
- A hit creates new ingress evidence, references the source request, and reports
  zero fresh provider tokens/cost.
- Redis single-flight leases reduce miss stampedes.
- Fencing tokens and durable namespace/exact-key generations stop stale or slow
  writers from repopulating invalidated entries.
- Operator commands approve/revoke cache access and perform audited exact-key or
  namespace invalidation.
- Cache corruption, expiry, ineligible models, or invalid extraction content
  produce a miss rather than unsafe success.

## Evaluation and promotion gates

- Versioned synthetic datasets include ordinary, ambiguous, malformed,
  long-context, and adversarial cases.
- Dataset files and profiles are canonicalized and hashed; private-looking or
  malformed fixture content is rejected.
- Evaluation runs, cases, code revision, model/policy/pricing versions, sampling
  configuration, prompt/scorer identity, and mutable-model limitations are
  recorded durably.
- `POST /v1/evaluations/runs` enqueues an authorized run; a separate worker from
  the same image claims one case at a time.
- Evaluation uses normal admission and accounting with cache bypass and a
  distinct traffic marker.
- Interrupted or uncertain cases are not blindly redispatched.
- Scoring includes extraction validity and field accuracy, classification
  precision/recall/F1, and a documented human-review path for generation.
- Failed requests, timeouts, truncations, and invalid results remain in
  denominators instead of disappearing from reports.
- Versioned promotion gates compare an approved baseline with task quality,
  latency, cost, and required evidence.
- Missing baseline, human review, or required evidence blocks promotion rather
  than implicitly passing.
- Failed gates leave the previous policy active.

## Observability and operational controls

- Prometheus-compatible metrics use bounded label sets.
- OpenTelemetry spans are sampled and can export over approved HTTPS/localhost
  endpoints.
- Exporter failure is best effort, observable, and does not replace durable
  request/attempt records or break otherwise valid responses.
- Structured events are sanitized and do not include raw prompts, outputs, or
  keys.
- Tenant-scoped summary queries distinguish gateway requests, completions,
  success rate, latency percentiles, provider attempts/errors, retries,
  fallback, cache hits, idempotency replay, tokens, fresh estimated spend, and
  unknown-usage attempts.
- Application and evaluation traffic are queryable separately.
- Operator-only `/metrics` access is distinct from tenant summaries.
- Versioned offline alert rules and runbooks cover elevated failures, high
  latency, high spend, validation failures, open circuits, exporter loss, and
  budget exhaustion.
- Alert evaluation treats missing signals or insufficient observation windows as
  missing evidence—not as healthy measurements.

## Operator command-line surface

The `gateway` CLI provides audited or bounded administration for:

- Waiting for database readiness during startup.
- Seeding/verifying a private synthetic local credential.
- Inspecting authorized privacy-safe request evidence.
- Revoking credentials.
- Publishing immutable policy/model/pricing versions.
- Activating and rolling back policy versions.
- Enabling and disabling providers.
- Conservatively reconciling unknown attempts.
- Approving and revoking tenant cache eligibility.
- Invalidating exact cache identities or application namespaces.
- Querying tenant spend summaries.
- Purging expired protected content and eligible metadata.
- Running the separate evaluation worker.
- Recording human generation review.
- Approving immutable evaluation baselines.
- Evaluating sanitized signals against versioned alert rules.

## Local container and startup behavior

- Compose starts PostgreSQL 16, Redis 7, and the gateway on loopback-only ports.
- Health checks wait for PostgreSQL and expose API liveness.
- The application waits for database availability with a bounded timeout.
- Local Compose runs migrations by default.
- Staging configuration can disable automatic migrations so a separate one-off
  migration task owns schema changes before the API service starts.
- Invalid migration-control configuration fails closed.
- The image runs as a non-root user.
- The quickstart performs authenticated generation and extraction smoke checks
  and returns correlated request IDs without printing secrets.

## Automated testing and CI evidence

### Current verified result

- **279 tests collected and passed locally.**
- The suite includes unit, property-style, API, provider contract, sanitized
  fixture, real PostgreSQL integration, Redis coordination, fault injection,
  concurrency, security, migration, evaluation, benchmark, documentation-link,
  startup, and telemetry tests.
- Every PostgreSQL integration fixture creates a random isolated schema and
  removes only its own schema.
- Redis tests use isolated random key namespaces.
- No required integration test is silently skipped when PostgreSQL or Redis is
  unavailable; the suite fails visibly.
- Ruff formatting and linting pass.
- Strict mypy passes across application, migrations, tests, and deployment code.
- `uv audit --locked` previously checked 67 locked packages with no reported
  known vulnerability/adverse status.
- Strict OpenSpec validation passes.
- Rebuilt local containers pass readiness plus authenticated generation and
  extraction smoke tests.
- Browser verification confirmed the generated Swagger/OpenAPI surface and
  typed unauthenticated behavior without entering a private API key.

### GitHub Actions pipeline

The successful workflow at
<https://github.com/gultandon/Llm-gateway/actions/runs/35556182447> contains:

- Locked dependency installation.
- Dependency audit.
- Ruff format and lint checks.
- Strict mypy.
- Focused OpenAPI compatibility, gate, alert, and benchmark tests.
- Full PostgreSQL/Redis test suite.
- Reproducible benchmark execution.
- Strict OpenSpec validation.
- Docker Compose image build and authenticated API/evaluation smoke demo.
- Gitleaks repository secret scan.
- Terraform formatting and validation for both bootstrap and staging stacks
  without cloud credentials.

This is CI, not a completed continuous-deployment pipeline.

## Synthetic benchmark evidence

The published benchmark is deliberately small and offline. It exists to make
measurement reproducible, not to simulate production traffic.

- Seven synthetic cases, repeated twice per condition: 14 requests per
  condition.
- Three conditions: direct fixed mock, gateway with cache bypass, and gateway
  with repeated exact-cache eligibility.
- All failures remain in denominators.
- Direct fixed mock: 8/14 successful, 14 mock invocations, p95 0.089 ms.
- Gateway cache bypass: 8/14 successful, 14 invocations, p95 end-to-end 53.614
  ms and p95 measured gateway overhead 53.597 ms.
- Gateway repeated exact cache: 8/14 successful, 10 invocations, four hits
  (28.6%), p95 end-to-end 45.302 ms and p95 gateway overhead 45.288 ms.
- Extraction validity was 40% and field accuracy 75% because the dataset
  intentionally includes malformed/ambiguous cases and the mock is not an LLM.
- Two generation cases still require human review.
- Classification macro-F1 was zero for this mock fixture.
- Cost was USD 0, so no inference-cost reduction can be calculated.

Permissible resume use, if space warrants: “Published a reproducible offline
benchmark that measured 53.6 ms p95 gateway overhead and a 28.6% exact-cache hit
rate on a declared 14-request-per-condition synthetic workload.” Never present
these as production, staging, live-model, or statistically general results.

## AWS Terraform design

### Reviewed plans

- State bootstrap plan: **6 additions, 0 changes, 0 destroys**.
- EKS staging foundation plan: **78 additions, 0 changes, 0 destroys**.
- Target account: `365712037872`.
- Target region: `ap-south-1`.
- Terraform provider has an account allowlist guard so planning/applying in a
  different account fails.
- Both plans passed formatting and validation and were generated using read-only
  AWS discovery.

### State bootstrap stack

After successful application and verification, this can be described as a
Terraform state foundation containing:

- Dedicated S3 state bucket.
- Public-access blocks.
- AES-256 server-side encryption.
- Bucket versioning.
- TLS-only bucket policy.
- 90-day expiry for noncurrent state versions and cleanup of incomplete
  multipart uploads.
- `prevent_destroy` protection.

Bootstrap begins with local state by necessity. The state must then be placed
under secure custody. The main stack must be initialized against the remote
backend and planned again; the original local-backend plan must not be reused.

### Staging foundation stack

After successful application and resource inspection, the following foundation
can be described as provisioned:

- One tagged VPC (`10.86.0.0/16`) across two availability zones.
- Six subnets: two public, two private application, and two private data
  subnets.
- Separate public, application, and data route tables.
- Internet gateway and public route for the public tier.
- No NAT gateway and no general internet route for application/data tiers.
- Security groups and explicit rules limiting traffic from EKS nodes to
  PostgreSQL, Redis, and VPC endpoints.
- Interface endpoints for EC2, ECR API, ECR Docker registry, EKS, EKS Auth,
  Secrets Manager, and STS.
- S3 gateway endpoint for private object access.
- Private RDS PostgreSQL 16.15 on `db.t4g.micro`, 20 GiB gp3 storage with
  autoscaling capped at 40 GiB, encryption at rest, forced TLS, seven-day
  automated backups, deletion protection, final snapshot, and
  `prevent_destroy`.
- Private Redis 7.1 on one `cache.t4g.micro` node with encryption in transit and
  at rest, one-day snapshots, and `prevent_destroy`.
- Private, encrypted, versioned S3 evaluation-artifact bucket with public access
  blocked, TLS required, 30-day current-object retention, and seven-day
  noncurrent-version retention.
- ECR repository with immutable tags, scan-on-push, AES-256 encryption, and a
  lifecycle retaining the newest 20 images.
- EKS control-plane CloudWatch log group with seven-day retention.
- Seven Secrets Manager secret containers for the database URL, replay key,
  cache key, input-hash key, TLS certificate, TLS key, and staging-smoke client
  key. Terraform
  intentionally does not store their values.
- EKS 1.35 control plane with private API access by default, KMS envelope
  encryption, deletion protection, and all control-plane log types enabled.
- Two on-demand `t4g.medium` ARM64 managed nodes across private application
  subnets, encrypted 30 GiB gp3 roots, IMDSv2 enforcement, node repair, and a
  two-to-three node scaling range.
- Pinned VPC CNI, kube-proxy, CoreDNS, Pod Identity, and AWS Secrets Store CSI
  add-ons, including VPC CNI NetworkPolicy enforcement.
- EKS access entry for the reviewed administrator and separate Pod Identity-
  bound API/smoke IAM roles; the API role cannot read the smoke client key.
- A provisional monthly AWS Budget resource set to USD 100 by default.

### Important post-apply limitations

Applying the reviewed foundation does not mean the application is deployed:

- No image has been built, scanned in ECR, pushed, or selected by digest.
- No Kubernetes workload has been applied to a cluster.
- No public/internal load balancer or Route 53 hosted zone/record is available;
  the defined Service is private `ClusterIP` only.
- No migration Job, API Deployment, or evaluation Job has run.
- No staging migrations or smoke tests have run in AWS.
- No live provider egress exists; private tasks have no NAT/internet route.
- Secret containers exist but secret versions/values are not populated by
  Terraform.
- Redis has network isolation and TLS but no application authentication token.
- RDS and Redis are single-AZ staging choices, not high-availability production
  design.
- The USD 100 AWS Budget is alert-only, not a hard limit. The reviewed input has
  no alert email and the budget is account-wide rather than project-isolated.
- Load-balancer access logs, WAF, VPC Flow Logs, automated secret rotation, CloudWatch
  infrastructure alarms, load tests, restore drills, and rollback exercises
  remain open.
- The application has not yet been wired to store evaluation artifacts in S3.

### Kubernetes runtime design ready after infrastructure apply

Versioned Kubernetes templates define namespace prerequisites, Pod Identity-
backed Secrets Store CSI mounts, default-deny/bounded-egress NetworkPolicies, a
bounded migration Job, two-replica API Deployment, private HTTPS ClusterIP
Service, PodDisruptionBudget, and opt-in evaluation Job. Containers run as UID
10001 with a read-only root filesystem, dropped capabilities, resource bounds,
topology spread, and HTTPS liveness/readiness probes. A tested renderer accepts
only reviewed Terraform outputs and an immutable `sha256` ECR digest and refuses
live-provider configuration. This must not be described as deployed until the
cluster, secrets, digest, migration, rollout, and smoke checks succeed.

## What can be claimed after tonight's reviewed foundation apply

Use wording similar to the following only after both applies report success and
the resources are inspected:

> Provisioned an AWS staging foundation with Terraform across two availability
> zones, including private RDS PostgreSQL and Redis, isolated application/data
> subnets, EKS with private ARM64 nodes, Pod Identity, VPC endpoints, ECR,
> Secrets Manager, encrypted
> versioned S3 storage, CloudWatch retention, and protected remote Terraform
> state.

Optionally add the plan evidence:

> Reviewed and applied account-bound Terraform plans comprising a six-resource
> state bootstrap and a 78-resource EKS staging foundation with no modifications or
> deletions to existing AWS resources.

Do not use “deployed the gateway to EKS” after the foundation apply. That claim
requires workload rollout and verification described above.

## Resume-ready accomplishment themes

These are accurate themes from which a resume agent can select concise bullets.

### Backend/platform engineering

- Built a provider-neutral asynchronous FastAPI gateway for authenticated text
  generation and locally schema-validated extraction.
- Designed explicit domain boundaries that normalize two vendor SDKs without
  leaking provider types into API contracts.
- Created nine PostgreSQL migrations and 19 durable tables for tenancy,
  configuration, requests, attempts, usage, audit, idempotency, budgets, cache
  generations, and evaluation.

### Reliability engineering

- Implemented one monotonic end-to-end deadline, bounded full-jitter retry,
  ordered fallback, typed failure classification, and deterministic error
  precedence.
- Built Redis-shared rate/concurrency admission and provider-domain circuit
  breakers with fenced half-open probes and fail-closed behavior.
- Modeled timeout/disconnect uncertainty conservatively rather than claiming
  exactly-once external execution.

### Financial correctness

- Implemented transactional UTC daily/monthly spend buckets and per-attempt
  reservations that prevent concurrent over-admission.
- Reconciled known usage idempotently and retained conservative holds for
  missing or uncertain usage.
- Captured immutable pricing identity and every retry/fallback attempt in durable
  accounting.

### Security and privacy

- Implemented hashed tenant credentials, role-based operator actions, audited
  configuration mutations, and cross-tenant isolation.
- Protected replay/cache content with AES-GCM and avoided raw prompt/output
  persistence and logging by default.
- Added bounded schema validation, size/depth controls, secret scanning, private
  data services, least-privilege IAM, and fail-closed critical dependencies.

### Caching and concurrency

- Built opt-in exact caching with canonical versioned identity, encrypted
  entries, local revalidation, one-hour TTL, and zero-fresh-spend provenance.
- Prevented stampedes and stale invalidation races with Redis single-flight
  leases, fencing tokens, and durable namespace/exact-key generations.

### Evaluation and observability

- Built versioned datasets, durable evaluation runs, task-specific scorers,
  human-review records, immutable baselines, and promotion gates that block on
  missing evidence.
- Added tenant-scoped operational summaries, bounded metrics, OpenTelemetry
  traces, sanitized events, alert rules, and incident runbooks.
- Published a reproducible local synthetic benchmark with raw sanitized evidence
  and explicit limitations.

### Quality and delivery

- Built a 279-test suite spanning API, contracts, PostgreSQL, Redis,
  concurrency, fault injection, privacy, spend, caching, evaluation, telemetry,
  migrations, and startup.
- Created GitHub Actions checks for formatting, linting, strict typing,
  dependency audit, full integration tests, OpenAPI compatibility, container
  smoke, evaluation regression, secret scanning, OpenSpec, and Terraform.
- Maintained a reproducible non-root containerized local environment.

### Cloud infrastructure — conditional after apply

- Defined and, after successful verification, provisioned an account-bound AWS
  staging foundation through Terraform.
- Isolated application and data tiers across two AZs with explicit security
  group paths and private service endpoints.
- Provisioned encrypted private data/artifact services, immutable image storage,
  protected state, bounded retention, and least-privilege EKS access/workload roles.

## Suggested project title and technology line

**LLM Reliability Gateway — Backend and Cloud Platform Engineer**

Python, FastAPI, PostgreSQL, Redis, SQLAlchemy, Alembic, OpenAI SDK, Anthropic
SDK, JSON Schema, Docker, Kubernetes, Amazon EKS, AWS, Terraform, RDS, ElastiCache,
S3, ECR, IAM, Secrets Manager, CloudWatch, GitHub Actions, OpenTelemetry,
Prometheus, Pytest, Ruff, mypy, uv, OpenSpec

Kubernetes/EKS workload and infrastructure code is implemented and locally
validated, but must be described as designed rather than deployed until apply
and cluster evidence exist.

## Strong resume bullet candidates

The resume agent should normally choose four or five rather than using all of
them.

1. Built a provider-neutral asynchronous FastAPI gateway for authenticated LLM
   generation and schema-validated extraction, normalizing OpenAI and Anthropic
   SDK behavior behind fixture-tested domain contracts.
2. Implemented deterministic versioned routing with capability, health,
   deadline, and spend exclusions, plus bounded retry/fallback and explainable
   candidate evidence for every request.
3. Designed Redis-shared rate/concurrency admission and circuit breakers with
   fenced half-open probes, lease recovery, and fail-closed behavior across
   simulated replicas.
4. Built PostgreSQL-backed UTC spend reservations and idempotent usage
   reconciliation so retries, fallbacks, crashes, and unknown provider usage
   remain bounded and attributable under concurrency.
5. Implemented tenant-isolated encrypted idempotency replay and opt-in exact
   caching with AES-GCM, TTL enforcement, schema revalidation, single-flight
   coordination, and race-safe invalidation.
6. Developed versioned synthetic evaluation, scoring, human-review, baseline,
   and promotion-gate workflows, with missing evidence explicitly blocking
   policy activation.
7. Created 279 automated tests across APIs, adapters, PostgreSQL, Redis,
   concurrency, fault recovery, security, spend, cache, evaluation, and
   telemetry; enforced them through a passing GitHub Actions pipeline with
   dependency and secret scans.
8. Published a reproducible offline benchmark measuring 53.6 ms p95 gateway
   overhead and a 28.6% exact-cache hit rate on a declared 14-request-per-condition
   synthetic workload.
9. **Use after successful foundation apply:** Provisioned a two-AZ AWS staging
   foundation with Terraform, including private RDS PostgreSQL and Redis, VPC
   endpoints, ECR, Secrets Manager, encrypted versioned S3, CloudWatch retention,
   least-privilege EKS access/workload IAM, and protected remote state.

## Claims the resume agent must not make yet

- Do not claim production deployment, production traffic, customers, users, or
  revenue impact.
- Do not claim the API is deployed on EKS merely because the foundation plan was
  applied.
- Do not claim live OpenAI or Anthropic calls, live-model quality, or provider
  invoice savings.
- Do not claim a production uptime percentage, production latency SLO, high
  availability, multi-region behavior, or autoscaling evidence.
- Do not claim Kubernetes ran successfully in AWS before the cluster rollout is
  exercised; repository implementation and static validation are claimable.
- Do not claim exactly-once provider execution.
- Do not claim the gateway can guarantee provider invoice ceilings.
- Do not claim semantic caching; the implemented cache is exact identity-based
  reuse.
- Do not generalize the 28.6% hit rate or 53.6 ms p95 overhead beyond the small
  synthetic local benchmark.
- Do not claim every one of the 279 tests is a unit test; the count includes
  unit, API, integration, contract, fault, and other test categories.
- Do not claim a CI/CD deployment pipeline. CI is implemented; cloud deployment
  and promotion automation are not complete.
- Do not claim zero infrastructure drift until post-apply state and a fresh
  no-change plan are verified.
- Do not claim Terraform controls all secret values. It creates secret
  containers, while secret population/rotation remains an operator step.

## Remaining project work after the foundation apply

- Complete the threat model and architecture decision records for trust
  boundaries and residual risk.
- Build the immutable ARM64 service image, scan it, push it to ECR, and pin the
  digest.
- Establish a reviewed private Kubernetes API access path.
- Populate and rotate database, replay, cache, input-hash, TLS certificate, and
  TLS key secrets without
  placing plaintext in Terraform state.
- Create a least-privilege application database user/URL.
- Render manifests from applied Terraform outputs and the immutable digest;
  apply prerequisites, migration Job, API Deployment/Service/PDB, and opt-in
  evaluation Job in that order.
- Run one-off migrations, readiness checks, authenticated mock staging smoke,
  and clean-environment verification.
- Decide whether a load balancer/public DNS is required; if so, separately
  design its controller IAM, TLS, network exposure, access logs, and WAF policy.
- Add VPC Flow Logs decision, Redis authentication,
  secret rotation, and infrastructure alarms as appropriate.
- Exercise failed rollout, prior-image/policy rollback, database compatibility,
  backup restore, dependency outage, tenant isolation, and load behavior.
- Wire evaluation artifacts to S3 and define worker scheduling/orchestration.
- Approve a real evaluation baseline and complete generation human review.
- Establish conservative live-provider token bounds before any strict-budget
  dispatch.
- Obtain separate explicit authorization and a recorded paid-provider spending
  ceiling before any live call.
- Complete release evidence and only then consider synchronizing/archiving the
  full OpenSpec program.

## Evidence references

- [Repository status and quickstart](../README.md)
- [Architecture and design decisions](../openspec/changes/build-llm-reliability-gateway/design.md)
- [Milestone task status](../openspec/changes/build-llm-reliability-gateway/tasks.md)
- [Foundation verification](milestone-1-verification.md)
- [Provider/routing verification](milestone-2-verification.md)
- [Reliability verification](milestone-3-verification.md)
- [Budget/cache verification](milestone-4-verification.md)
- [Evaluation/observability verification](milestone-5-verification.md)
- [Synthetic benchmark and limitations](milestone-5-benchmark.md)
- [AWS Terraform plan review](staging-plan-review.md)
- [Terraform reproduction and activation procedure](../infra/terraform/README.md)
- [EKS deployment and rollback runbook](eks-deployment.md)
- [Provider selection and fixture provenance](provider-selection.md)
- [Alert rules and operational response](operations/alerts.md)
