# Learning checklist: LLM Reliability Gateway

This is an ordered, project-specific checklist, not a course or a calendar. Learn a topic, use it in a small change, and move on when you meet its success criteria. Looking up syntax and using documentation still counts as knowing enough. Reading a resource alone does not.

Prepared on 14 September 2026 from the [project spec](../LLM-Reliability-Gateway-Spec.md), the active change's [capability contracts](../openspec/changes/build-llm-reliability-gateway/proposal.md#capabilities), [design](../openspec/changes/build-llm-reliability-gateway/design.md), [tasks](../openspec/changes/build-llm-reliability-gateway/tasks.md), and your [resume](../sources/resume-gul_tandon.md). Resources below are official documentation or engineering articles; read the named sections, not entire sites. Online documentation may describe newer releases: use this repository's locked versions when implementing.

## Your starting point

I treat your resume as your stated background, not an independent assessment of proficiency or verification of its metrics.

| Existing background | How to use it here | Main addition |
| --- | --- | --- |
| Python, automation, Flask APIs, direct HTTP/API work | Read the service and build adapters | Async execution, type contracts, FastAPI/Pydantic |
| SQL, MySQL, RDS | Understand entities and queries | PostgreSQL transactions, async SQLAlchemy, migrations, concurrent correctness |
| Fallbacks in automation systems | Recognize partial failure | Deadline and attempt bounds, uncertain execution, shared controls |
| AWS, Docker, Terraform, CI/CD | Run local dependencies; later understand deployment | EKS operations and safe schema/policy rollout/rollback |
| CloudWatch and structured logging | Diagnose requests and failures | Traces, bounded metric labels, reproducible LLM evaluations |

Skip beginner Python, SQL, REST, Docker, and AWS courses unless the practical checks reveal a gap. Your resume does not establish experience with asyncio, LLM APIs, Redis coordination, or evaluation; those get explicit checklist items.

The repository has an implemented offline foundation: authenticated mock generation/extraction, local schema validation, PostgreSQL evidence, and containers/tests. Two live adapters, routing, retries, circuits, idempotency, cache, budget reservations, evaluation, and staging are still planned. Some older repository text says no resume is present; the referenced resume now exists. No source reference was changed for this document.

## How to work through this

**Start coding during items 1–6.** Items 7–9 prepare you for the next planned milestone: offline provider adapters and routing. Items 10–16 are learned as their implementation milestones arrive. Item 17 builds on your cloud background and can wait. Item 18 is useful as soon as you have one change you understand and can demonstrate; it is not blocked on completing this checklist.

For the next few days, a useful stopping point is an offline adapter or deterministic routing slice, its failure tests, and a clear explanation of the tradeoffs. Completing the full gateway is not a prerequisite for applying to jobs. These checks establish project readiness; continue general coding/SQL practice and role-specific interview preparation alongside applications.

## Ordered checklist

### 1. [ ] Understand the gateway and run its existing foundation

**Learn:** What a provider-neutral gateway does; the difference between the API, domain, adapter, and persistence layers; implemented behavior versus planned behavior. Follow one request from authentication to mock invocation to durable terminal evidence. Learn the local `uv`, migration, container, and test workflow only as needed.

**Why:** You need to extend the actual service rather than rebuild working pieces or assume future controls already exist.

**Resources:** [Quickstart](quickstart.md), [foundation verification](milestone-1-verification.md), and [milestone tasks](../openspec/changes/build-llm-reliability-gateway/tasks.md). Read `app/main.py`, `app/service.py`, and `app/providers/mock.py` in that order.

**Enough when:** You can run the offline smoke demo, inspect a request by ID, demonstrate one invalid extraction, and explain why the mock is not measuring real LLM quality or cost. You can locate the test for the behavior you want to change. You understand that PostgreSQL is required for the full suite, while unit tests alone are narrower evidence.

### 2. [ ] Async Python and cancellation

**Learn:** Coroutines, `await`, task scheduling, concurrent versus sequential I/O, async context managers, `try/finally` cleanup, cancellation propagation, and timeouts. Recognize blocking work inside an async request handler. Learn a monotonic clock's purpose; detailed deadline orchestration comes in item 10.

**Why:** Provider and database calls wait on I/O. Blocking the event loop hurts unrelated requests, and mishandled cancellation can leak capacity or start unwanted attempts.

**Resources:** [Python 3.12: Coroutines and Tasks](https://docs.python.org/3.12/library/asyncio-task.html), focusing on coroutines, task cancellation, task groups, and timeouts; [FastAPI's async explanation](https://fastapi.tiangolo.com/async/).

**Enough when:** A small exercise runs two delayed mock calls concurrently; cancelling one runs cleanup and propagates cancellation. You can explain why `time.sleep()` in an async handler blocks progress and why cancelling a local task cannot prove a provider stopped processing or billing.

### 3. [ ] Typed contracts and boundaries

**Learn:** Type hints, unions/optional values, dataclasses, enums, `Protocol`, dependency injection, and domain exceptions. Distinguish static checking from runtime validation. Understand immutable snapshots, including the limitation that freezing a dataclass does not freeze nested mutable objects.

**Why:** The same gateway should work with different providers and test doubles without exposing SDK types or allowing configuration changes to rewrite in-flight decisions.

**Resources:** [Python typing documentation](https://docs.python.org/3.12/library/typing.html), particularly `Protocol`; the existing contracts in `app/domain/models.py` and doubles in `tests/fakes.py`. Use the project's type-checker errors as targeted exercises.

**Enough when:** You can add a tiny fake implementing the existing `Provider` contract, inject it without rewriting the service, and keep type checks passing. You can explain why SDK exceptions are converted inside adapters and why API/domain contracts should not depend on ORM models.

### 4. [ ] FastAPI/Pydantic request and error contracts

**Learn:** Request/response models, field bounds, validation/coercion, dependencies, middleware, application lifespan, OpenAPI, and consistent HTTP errors. Understand caller input versus authenticated identity and the distinction between client-safe retry and upstream retry classification.

**Why:** Invalid requests must fail before provider work; provider substitution should preserve a stable public API.

**Resources:** [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/); `app/api/schemas.py`, `app/api/middleware.py`, `app/main.py`, and `tests/test_api.py`. Use the running service's `/docs` and `/openapi.json` as the current wire contract.

**Enough when:** You can trace a valid request and a 422 response, explain 401 versus 403, and write a boundary test proving an invalid request makes no provider call. You can explain why a provider credential error is not a client-authentication 401.

### 5. [ ] LLM API fundamentals and trustworthy extraction

**Learn:** Generation versus extraction; input/output tokens, context windows, output limits, temperature, model identifiers, finish reasons, refusals, missing usage, and token-based estimates. Learn JSON parsing versus JSON Schema validation: required fields, types, enums, limits, local references, and schema versions. Schema-valid output can still be factually wrong.

**Why:** A JSON-looking response is not a safe extraction result. Provider-native structured output cannot replace local validation, and the gateway needs model limits before selecting candidates.

**Resources:** [Gemini text generation](https://ai.google.dev/gemini-api/docs/text-generation) for a concrete API example; [JSON Schema reference](https://json-schema.org/understanding-json-schema/reference), focusing on objects, types, and numeric/string limits; `app/api/schema_validation.py` and `tests/test_schema_validation.py`. The project's bounded schema subset is narrower than the full standard.

**Enough when:** You can describe a provider request/response without making a paid call, write a small extraction schema, and distinguish malformed JSON, valid JSON with incorrect fields, refusal, and truncation. Tests reject each unsuccessful case. You can explain why remote schema references are forbidden and why temperature zero is not a guarantee of identical future outputs.

### 6. [ ] Testing behavior, including failures

**Learn:** pytest fixtures and parametrization, async tests, fakes versus transport mocks, adapter contract tests, integration tests, and fault injection. Learn to assert observable behavior and side effects, not just returned values.

**Why:** An offline fake must prove error classification and accounting behavior; a successful demo alone does not prove reliability.

**Resources:** [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html); `tests/test_provider_contract.py`, `tests/test_api.py`, `tests/test_postgres.py`, and `tests/conftest.py`. Copy the repository's fixture patterns rather than constructing a new test framework.

**Enough when:** You add one meaningful failure test that fails when the safety behavior is deliberately broken and passes after restoring it. You can explain what a fake cannot establish, why database tests use isolated random schemas, and why offline adapter fixtures prove compatibility rather than live model quality.

You can now contribute to the foundation. Choose a small change and use the remaining topics to support it.

### 7. [ ] PostgreSQL persistence and transaction basics

**Learn:** Async SQLAlchemy sessions, transaction boundaries, commit/rollback, constraints, unique event identities, indexes, and forward Alembic migrations. Compare PostgreSQL behavior with your MySQL experience. Learn enough isolation and row locking to recognize a read-then-write race; apply it deeply when implementing budgets.

**Why:** Request and usage records are correctness state. Dispatch intent must exist before an upstream call; terminal persistence must succeed before the gateway claims success.

**Resources:** [SQLAlchemy async guide](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html), particularly sessions and concurrent tasks; [PostgreSQL 16 transaction isolation](https://www.postgresql.org/docs/16/transaction-iso.html); [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html). Read `app/persistence/store.py` and the foundation migration alongside them.

**Enough when:** You can explain the existing begin/finish transactions, rollback on a failure, and the unique usage-event constraint. You know why independent concurrent tasks should not share an `AsyncSession`. You can write and test a small forward migration in an isolated local schema without changing the frozen foundation migration.

### 8. [ ] Provider adapters and HTTP failure normalization

**Learn:** Translate domain inputs into provider requests and responses into normalized results. Map timeout, connection failure, 429, transient 5xx, credential failure, invalid request, refusal, and missing usage. Learn HTTP client pooling/timeouts and the chosen SDK's automatic retry behavior.

**Why:** Hidden SDK retries multiply attempts and spend; inconsistent errors make safe fallback impossible.

**Resources:** [Claude API overview](https://platform.claude.com/docs/en/api/overview) and [Gemini generation documentation](https://ai.google.dev/gemini-api/docs/text-generation) as example provider contracts, not a mandated provider selection; [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/). Start with the existing `Provider` interface and common contract suite. Read the pinned SDK's retry documentation once providers are selected.

**Enough when:** One adapter passes the common suite using synthetic transport responses without credentials or network calls. It preserves missing usage as unknown, sanitizes wire errors, and has no uncounted SDK retries. A second adapter can use the same tests and public result types.

### 9. [ ] Versioned pricing and deterministic routing

**Learn:** Conservative token/output bounds, decimal monetary arithmetic, immutable model/policy/pricing snapshots, eligibility filtering before ranking, fixed score normalization, stable tie-breaking, and exclusion explanations. Learn validated operator publish/activate/rollback commands, audit events, and bounded configuration refresh.

**Why:** A cheap model is useless if it cannot meet schema, tenant, quality, context, deadline, or spend constraints. Decisions must remain explainable when configuration changes.

**Resources:** [Python Decimal](https://docs.python.org/3.12/library/decimal.html), especially construction from strings; [routing contract](../openspec/changes/build-llm-reliability-gateway/specs/provider-routing/spec.md); design decisions 3–5; tasks 2.4–2.7. The project spec supplies the routing algorithm; you do not need a machine-learning routing course.

**Enough when:** Given three synthetic candidates, you can calculate an estimate, reject an ineligible candidate, rank the others reproducibly, and explain a tie. Tests retain an in-flight snapshot across a policy update, reject unauthorized changes, and record disablement. You can explain that configured quality/latency scores are estimates and that monetary arithmetic cannot use binary floats.

**Practical near-term target:** One well-tested offline adapter or a routing slice is useful project progress and interview material. Continue applications while learning the later milestones.

### 10. [ ] Bounded retries, fallback, and one overall deadline

**Learn:** Transient versus permanent errors, bounded exponential backoff with jitter, `Retry-After`, per-attempt timeouts, total invocation caps, and a shared monotonic deadline covering admission, waits, calls, validation, and critical writes. Learn separate validation recovery and refusal handling.

**Why:** Recovery can otherwise turn one request into unlimited latency and billable attempts. Fallback must preserve the original constraints.

**Resources:** [AWS: Timeouts, retries, and backoff with jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/); [reliability contract](../openspec/changes/build-llm-reliability-gateway/specs/request-reliability/spec.md); design decision 4.

**Enough when:** Scripted 429/5xx/timeout tests recover only when time, attempts, and allowance permit. A too-long backoff starts no sleep or attempt, permanent invalid requests terminate, and cancellation starts no further calls. You can account for every invocation and explain the published scheduler tolerance and upstream billing uncertainty.

### 11. [ ] Shared rate limits, concurrency admission, and circuit breakers

**Learn:** Request-rate limits versus in-flight concurrency limits; closed/open/half-open circuits; bounded probes; Redis atomic operations, leases, expiry, and ownership checks. Understand why process-local counters cannot enforce limits across replicas.

**Why:** A provider outage or overloaded tenant should not consume every worker. Lost critical control state must not admit unlimited execution.

**Resources:** [Redis transactions](https://redis.io/docs/latest/develop/using-commands/transactions/) and [distributed lock patterns](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/), focusing on ownership, expiry, and failure assumptions; reliability tasks 3.4–3.5. These resources explain mechanisms; the project contract determines which guarantees you must test.

**Enough when:** Two simulated replicas respect the same limit and bounded half-open probes. Tests cover saturation, expired ownership, and Redis failure; waiting consumes the deadline. You can explain why Redis is optional in today's foundation but becomes critical when it owns admission controls.

### 12. [ ] Idempotency and uncertain execution

**Learn:** Tenant/endpoint/key scope, request fingerprints, atomic ownership, in-progress/completed/failed/uncertain states, protected replay content, retention, and reconciliation after a crash. Distinguish replaying a terminal failure from blindly retrying upstream work.

**Why:** Client retries and worker crashes can duplicate paid execution. An expired lease proves lost ownership, not that a provider did nothing.

**Resources:** [AWS: Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/); the project's [idempotency contract](../openspec/changes/build-llm-reliability-gateway/specs/request-reliability/spec.md#requirement-tenant-scoped-idempotency-state); tasks 3.6–3.7. Item 15 supplies the protection/retention details needed before shipping replay.

**Enough when:** Concurrent identical requests dispatch one sequence; changed input conflicts; another tenant cannot see the record; completed requests replay with a new ingress ID and original execution provenance. Crash-after-dispatch tests block redispatch until reconciliation. You can explain why this is not exactly-once external execution and why an expired key can incur new spend.

### 13. [ ] Atomic spend reservations and reconciliation

**Learn:** Reserve conservative maximum estimated liability before each dispatch; count committed cost plus outstanding holds; lock request and tenant UTC budget buckets in a consistent order; reconcile once using stable event identity. Track unknown usage, overruns, and original period buckets across rollover.

**Why:** Checking totals after a call races under concurrency. A timeout may have incurred cost, and fallback spend includes failed attempts too.

**Resources:** [Usage/budget contract](../openspec/changes/build-llm-reliability-gateway/specs/usage-budgets/spec.md); design decision 5; [PostgreSQL transaction isolation](https://www.postgresql.org/docs/16/transaction-iso.html). Extend the transaction knowledge from item 7 rather than learning accounting as a separate course.

**Enough when:** If allowance is $0.10 and two concurrent calls each require a $0.08 reservation, at most one dispatches. Duplicate reconciliation charges once; missing usage retains a hold; retry/fallback share the original ceiling; midnight does not erase a hold. Database failure before dispatch makes no call, and failed terminal recording cannot return unrecorded success. You can distinguish estimated liability from an actual invoice guarantee.

### 14. [ ] Exact caching, invalidation, and single-flight

**Learn:** Canonical request identity, significant input whitespace, tenant/application isolation, schema/parameter/version keys, explicit eligibility, TTL, source versus fresh usage, and validation on cache reads. Learn cross-replica single-flight, fencing tokens, namespace/per-key generations, and stale-writer rejection.

**Why:** A wrong cache hit can leak another tenant's content or violate a schema. Deleting an entry is insufficient if a slow old writer can repopulate it.

**Resources:** [Cache contract](../openspec/changes/build-llm-reliability-gateway/specs/response-cache/spec.md); design decision 6; [Redis lock patterns](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/). Study the slow-writer and lost-owner scenarios before choosing an implementation.

**Enough when:** Tests separate tenant/schema/parameter/whitespace identities, reject expired/corrupt/ineligible results, and revalidate extraction hits. A hit has zero fresh provider spend and labeled source usage. An invalidation racing a slow writer cannot restore old content. You can distinguish exact reuse, idempotency, and semantic similarity; single-flight does not prove exactly-once provider execution.

### 15. [ ] Tenant security, privacy, and protected retention

**Learn:** Authentication versus authorization, deriving identity from credentials, least-privilege operator roles, credential hashing/revocation, HMAC identifiers versus encryption, protected cache/replay envelopes, external key management, retention, and backup/deletion limits. Treat model output as data, including apparent instructions and URLs.

**Why:** Ordinary metadata records should avoid raw content; cache/replay necessarily retain protected results. Every access path needs the same tenant boundary. Review these basics from item 1 onward; learn storage details when building items 12–14.

**Resources:** [OWASP authorization guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html) and [cryptographic storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html); [deployment/security contract](../openspec/changes/build-llm-reliability-gateway/specs/deployment-security/spec.md); `app/security/auth.py`.

**Enough when:** A short threat model identifies tenant, operator, provider, database, and protected-content boundaries. Tests reject cross-tenant schema/evidence/replay/artifact access and metadata spoofing; raw input/output/keys are absent from default diagnostics. You can explain where encryption keys live, how expired content becomes unreadable, and what backup deletion cannot immediately guarantee. Use established cryptographic libraries rather than designing cryptography.

### 16. [ ] Evaluation, observability, and honest measurement

**Learn:** Versioned synthetic datasets; schema validity versus field accuracy; classification precision/recall/F1; generation rubrics and human review; baseline/threshold profiles; and missing-evidence blocking. Learn durable run/case state and a separately launched runner that does not blindly repeat uncertain work. Connect logs, metrics, and traces; use bounded labels, latency distributions, and separate request/attempt/cache/replay/evaluation totals.

**Why:** A mock can prove failure behavior, not model quality. Averages and success-only cost totals can hide slow failures or expensive recovery; telemetry loss must not erase correctness evidence.

**Resources:** [Evaluation contract](../openspec/changes/build-llm-reliability-gateway/specs/evaluation-observability/spec.md), [measurement methodology](roadmap.md#benchmark-methodology), [scikit-learn evaluation reference](https://scikit-learn.org/stable/modules/model_evaluation.html) for classification metrics, [OpenTelemetry signals](https://opentelemetry.io/docs/concepts/signals/), and [Prometheus naming/labels guidance](https://prometheus.io/docs/practices/naming/). Read only the metric sections relevant to your task.

**Enough when:** A tiny synthetic report includes failures/timeouts in stated denominators, all attempt costs, unknown usage, versions, sample size, and environment. A failed required gate retains the old policy; missing baseline blocks promotion. A worker interruption preserves uncertain case evidence. You can trace one request, compute a fixture summary, and show exporter failure does not break a valid response. You can explain p95 versus average and why request IDs should not be metric labels. Neither mock latency nor the roadmap's targets become claimed live performance results.

### 17. [ ] Container service deployment and safe rollback — later

**Learn:** The differences between your Lambda experience and a long-running EKS workload: cluster/access management, scheduling, health probes, graceful shutdown, Pod Identity, CSI-mounted external secrets, NetworkPolicies, connection pools, private RDS/Redis, immutable image identity, resources/disruption budgets, and node/add-on upgrades. Learn expand/contract migrations, image/policy rollout rollback, backups/restore, smoke tests, and dependency-outage/load exercises.

**Why:** Deploying an old image does not reverse a database migration. Staging needs evidence that data compatibility and privacy survive failure and recovery.

**Resources:** [EKS deployment runbook](eks-deployment.md); design migration plan; tasks 6.1–6.7; [deployment contract](../openspec/changes/build-llm-reliability-gateway/specs/deployment-security/spec.md). Refresh the AWS services you already know only where the design differs.

**Enough when:** You can review a diagram and infrastructure plan explaining trust boundaries, costs, secrets, readiness, and rollback compatibility. A local image/policy rollback demonstrates preserved evidence. Cloud acceptance additionally requires explicitly authorized staging smoke, load, outage, restore, and rollback evidence; reading docs or passing Terraform validation is not that evidence. You can begin interviews before this milestone. Do not provision or make live provider calls merely to satisfy a learning checkbox.

### 18. [ ] Explain your own contribution in interviews — start early

**Learn:** Turn an implemented slice into a clear engineering explanation: problem, design, personal contribution, tradeoff, failure case, test evidence, and limitation. Separate existing project work from what you personally changed and understand. Learn the language of measured outcomes versus aspirations.

**Why:** You need to defend the work and reason about failures; completing every planned feature is unnecessary for a credible discussion.

**Resources:** Your change diff and tests; [foundation evidence](milestone-1-verification.md); [release evidence/project summaries guidance](roadmap.md#release-evidence-and-project-summaries). For this item, your own evidence is more valuable than another tutorial.

**Enough when:** Without reading a generated explanation, you can give a brief demo, sketch one request's flow, and answer these questions with examples from your work:

- Why use one async service and provider adapters?
- What happens after a timeout or crash, and what remains unknown?
- Which state must be committed before dispatch and before success?
- How do you prevent concurrent requests from overspending or sharing tenant data?
- Why does schema validity not establish task accuracy?
- What did you personally implement, what did tests establish, and what is still planned?

For features you have not built, describe the planned design and its risks plainly. Apply once you can explain and demonstrate your own completed slice; keep later checklist items as learning alongside the work.

## Topics to leave out of this learning path

Transformer mathematics, model training/fine-tuning, GPU serving, RAG/vector databases, agent frameworks, semantic caching, streaming, Kubernetes operator development, microservice decomposition, and multi-region failover are not baseline requirements. Add them only for a separately chosen role or future project extension. This project's immediate learning value comes from API contracts, async execution, failure handling, concurrent correctness, EKS workload operations, and defensible evidence.
