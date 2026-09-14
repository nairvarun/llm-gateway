## 1. Foundation — offline vertical slice

Coverage: `gateway-api`, mock contract in `provider-routing`, critical recording
in `usage-budgets`, and local/security foundations in `deployment-security`.
Keep live execution disabled until reliability and budget gates are complete.

- [x] 1.1 Create the Python package/dependency lock and Ruff/mypy/pytest configuration; verify installation and documented quality commands from a clean environment.
- [x] 1.2 Add provider-neutral inputs/results/errors and the scripted mock adapter; verify contract tests cover success, refusal, truncation, malformed output, missing usage, and all classified faults.
- [x] 1.3 Add PostgreSQL migrations for tenant credentials, immutable versions, requests, attempts, and usage/dispatch intent; verify clean migration and unique-event constraints in integration tests.
- [x] 1.4 Implement hashed client-key authentication, tenant/application identity, revocation, and operator permissions; verify invalid/revoked keys and cross-tenant access are denied.
- [x] 1.5 Publish generate/extract request/response/error OpenAPI schemas with configured bounds and enums; verify invalid fields, size limits, conflicting schema inputs, and unsupported modes cause no mock invocation.
- [x] 1.6 Implement bounded local JSON Schema validation and named schema versions; verify remote references, cycles, unsupported keywords, invalid/truncated/refused results never return extraction success.
- [x] 1.7 Implement the mock-backed generation/extraction path with durable request/attempt/terminal records; verify successful and failed calls are correlated and database failure prevents dispatch.
- [x] 1.8 Add liveness/readiness and containerized local dependencies; verify liveness ignores dependency outages and readiness reports critical versus optional failures without secrets.
- [x] 1.9 Add initial offline CI and an honest foundation quickstart; verify a clean credential-free checkout runs generation/extraction and passes format/lint/type/unit/API/contract checks.

## 2. Multi-provider routing — offline adapter evidence

Coverage: `provider-routing` and pricing provenance in `usage-budgets`.

- [ ] 2.1 Select two provider/model IDs and pinned SDKs against required capabilities; verify a recorded capability/pricing/token-bound matrix and sanitized fixture provenance exist, without paid calls by default.
- [ ] 2.2 Implement the first live adapter with SDK retries disabled or explicitly bounded; verify the common contract suite against sanitized fixtures covers normalization and error/usage classification.
- [ ] 2.3 Implement the second live adapter with the same boundaries; verify it passes the identical contract suite without credentials/network calls.
- [ ] 2.4 Implement immutable registry/policy/pricing resolution and conservative decimal token/cost estimates; verify pinned snapshots, unavailable-bound rejection, and estimate arithmetic tests.
- [ ] 2.5 Implement filtering, weighted deterministic ranking, and stable tie-breaking; verify capability/context/tenant/health/deadline/spend exclusions and property tests for identical-snapshot ordering.
- [ ] 2.6 Implement authorized policy publish/activate/rollback/provider-disable CLI using validated audited mutations; verify unauthorized writes fail, in-flight snapshots persist, and disablement takes effect within the refresh bound.
- [ ] 2.7 Connect routing evidence to API responses/durable records; verify each mock/fixture call is explainable from one request ID and no provider wire types leak into OpenAPI.

## 3. Reliability — bounded fault behavior

Coverage: `request-reliability` and failure contracts in `gateway-api`.

- [ ] 3.1 Implement one monotonic deadline, attempt timeouts, and recording margin with an injectable clock; verify no attempt/backoff starts beyond bounds and published scheduler-tolerance tests pass.
- [ ] 3.2 Implement classified retry, bounded jitter/Retry-After, total attempt caps, and ordered fallback; verify scripted 429/5xx/timeouts recover only within constraints and permanent invalid requests do not retry/fallback.
- [ ] 3.3 Implement separately classified schema-validation recovery/refusal behavior; verify valid recovery and exhausted deadline/attempt/spend failures use the documented error precedence.
- [ ] 3.4 Implement shared Redis circuit states and fenced half-open probes; verify open/half-open/closed transitions and probe limits across two simulated replicas.
- [ ] 3.5 Implement shared tenant/provider rate/concurrency admission; verify saturation, lease recovery, deadline-bounded waiting, and fail-closed control-store outages.
- [ ] 3.6 Add durable idempotency ownership/fingerprints and encrypted replay content; verify completed/failed replay, changed-input conflict, identical-concurrent ownership, retention expiry, and cross-tenant key isolation.
- [ ] 3.7 Implement disconnect/crash detection and uncertain-key recovery; verify no redispatch on expired leases, preserved dispatch evidence, and separately identified ingress versus original execution.
- [ ] 3.8 Add an integration fault matrix for timeouts, 429, selected 5xx, invalid output, provider disablement/outage, and critical-write failure; verify bounded behavior and record the evidence report.

## 4. Exact cache and budgets — concurrent correctness

Coverage: `response-cache`, full `usage-budgets`, and protected retention in
`deployment-security`. Complete this gate before enabling live-provider calls.

- [ ] 4.1 Add request/tenant UTC budget buckets, reservations, and transactional admission; verify competing replicas cannot over-admit a near-exhausted allowance and retries/fallback consume the same request ceiling.
- [ ] 4.2 Add idempotent reconciliation and unknown-usage holds/conservative recovery; verify failed attempts, duplicate events, overruns, midnight/month rollovers, and post-dispatch crashes are accounted once without assuming free work.
- [ ] 4.3 Wire reservation/dispatch/terminal transactions into every execution path; verify pre-dispatch persistence failure causes no provider call and post-dispatch failure never returns unrecorded success.
- [ ] 4.4 Implement exact canonical keying and opt-in eligibility with bypass/read-only/read-write modes; verify significant whitespace, schema/tenant/parameter/version changes remain isolated and sensitive/nondeterministic requests bypass.
- [ ] 4.5 Add encrypted cache entries, TTL/corruption checks, model eligibility rechecks, and extraction validation; verify expiry/invalid entries miss and valid hits report zero fresh provider usage plus source provenance.
- [ ] 4.6 Implement fenced single-flight and authorized exact/namespace invalidation; verify concurrent misses, owner loss, invalidation/slow-writer races, and waiter deadlines across replicas.
- [ ] 4.7 Add authorized spend queries and cache/control failure separation; verify cross-tenant queries fail and optional-cache degradation cannot bypass budget/idempotency/admission controls.
- [ ] 4.8 Implement retention/deletion for protected cache/replay content and metadata with documented backup limitations; verify expiry prevents serving old content and no raw prompt/output/key appears in default logs or records.
- [ ] 4.9 Run the integrated mock-backed reliability/cache/accounting/security gate; verify all corresponding spec scenarios pass, and permit any live smoke run only after explicit owner authorization and a recorded spending ceiling.

## 5. Evaluation and observability — reproducible evidence

Coverage: `evaluation-observability` and CI/secret checks in
`deployment-security`.

- [ ] 5.1 Create approved synthetic/sanitized versioned dataset manifests with easy, ambiguous, malformed, long-context, and adversarial cases; verify immutable hashes, schema checks, and no personal/secret data.
- [ ] 5.2 Add durable run/case records, authorized run APIs, and a separately launched runner; verify 202/status lifecycle, cross-tenant denial, restart/interruption handling, and no uncertain-case redispatch.
- [ ] 5.3 Implement extraction/classification scorers and a documented generation rubric/human-review record; verify fixture scores and inclusion of failures/timeouts in published denominators.
- [ ] 5.4 Add version-complete reports and configurable baseline/threshold profiles; verify quality/cost/latency comparisons, missing-evidence blocking, mutable-model limitations, and failed gates retaining the prior policy.
- [ ] 5.5 Add bounded-cardinality metrics, sanitized structured logs, and correlated traces; verify exporter outages do not break valid responses and sampled-out requests retain durable correlation evidence.
- [ ] 5.6 Implement authorized time-window summaries separating attempts, cache/replay, and evaluation traffic; verify summary denominators and fresh-spend totals against known fixtures.
- [ ] 5.7 Add alert rules/observation windows and runbooks for failure/latency/spend/validation/circuit/exporter/budget signals; verify scripted incidents produce the expected alert evidence and recovery instructions.
- [ ] 5.8 Connect offline evaluation/compatibility/dependency/secret gates to CI and expand the quickstart/demo; verify a credential-free checkout demonstrates evaluation regression failure and required checks never silently pass without evidence.
- [ ] 5.9 Run the documented benchmark workload (live only if explicitly authorized), store raw sanitized evidence/configuration, and publish targets versus results; verify every reported number is reproducible and unsupported resume claims remain absent.

## 6. Authorized staging — deployment and release evidence

Coverage: staging/security in `deployment-security` and all release gates.
This group is gated on explicit account/region/spend authorization, not just an
implementation request or a passing local test.

- [ ] 6.1 Deliver a threat model and ADRs for routing/reliability/cache/reservation/privacy choices; verify each documented trust boundary has corresponding tests or an explicit residual risk.
- [ ] 6.2 Build/tag immutable service/runner images and environment configuration; verify image scans, no embedded secrets, reproducible identity, and mock staging smoke support.
- [ ] 6.3 Add Terraform network/IAM/secret infrastructure and container/data/artifact resources; verify formatting/validation and an owner-reviewed account-specific plan with cost/retention limits before apply.
- [ ] 6.4 Document and exercise authorized staging provisioning/migrations/deployment; verify private data services, least-privilege access, TLS/secret rotation, and clean-environment smoke evidence.
- [ ] 6.5 Add explicit staging promotion with evaluation/security evidence and prior-image/policy rollback; verify a failed rollout blocks promotion and an exercised rollback preserves data/schema compatibility.
- [ ] 6.6 Add load/tenant-isolation/dependency-outage and restore exercises; verify published workload/concurrency, deadline/spend behavior, recovery evidence, and runbook completeness.
- [ ] 6.7 Assemble release documentation, API examples, measured benchmark, test evidence, and concise demo; verify every capability scenario has traceable evidence and README accurately states delivered scope before proposing sync/archive.
