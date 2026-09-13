# LLM Reliability Gateway

## Project specification

**Status:** Build specification  
**Audience:** Project owner, implementer, reviewer, and future interviewer  
**Primary objective:** Build production-style infrastructure that makes LLM-backed applications more reliable, observable, cost-aware, and testable.

## 1. Executive summary

The LLM Reliability Gateway is a Python service placed between client applications and multiple language-model providers. It exposes a stable API while handling authentication, request validation, model selection, retries, fallbacks, structured-output validation, semantic caching, budgets, tracing, and offline/online evaluation.

The project deliberately treats the LLM as one dependency inside a distributed backend system. Its differentiator is measurable operational behavior: every routing decision, failure, token count, cost estimate, latency measurement, cache decision, and evaluation result is recorded and queryable.

## 2. Problem statement

Direct model-provider integration creates recurring engineering problems:

- Provider APIs, credentials, response formats, and failure modes differ.
- A single model is rarely optimal for every request when quality, latency, and cost are all constraints.
- Timeouts, rate limits, malformed structured output, and transient provider failures can break applications.
- Token usage and spend are difficult to control across teams and environments.
- Prompt changes and model changes can silently reduce quality.
- Teams lack a consistent way to inspect requests, compare models, reproduce failures, and demonstrate reliability.

The gateway solves these problems through a provider-neutral contract and policy-driven control plane.

## 3. Goals

### Must achieve

1. Provide a versioned REST API for text generation and structured extraction.
2. Support at least two model providers through a common adapter interface and a deterministic mock provider for tests.
3. Route requests using task type, quality tier, estimated cost, latency target, provider health, and configured budgets.
4. Implement timeouts, bounded retries, exponential backoff, circuit breakers, and provider fallback.
5. Validate structured responses against JSON Schema or Pydantic models.
6. Provide semantic caching with explicit TTL, namespace, and invalidation behavior.
7. Record durable request, usage, cost, routing, and evaluation metadata.
8. Expose operational metrics, traces, and a small operator dashboard or query API.
9. Run repeatable evaluation datasets and compare model/policy versions.
10. Deploy reproducibly with Docker and Terraform on AWS.

### Non-goals

- A general-purpose chatbot UI.
- A broad RAG product, vector-search platform, or autonomous agent framework.
- Training or fine-tuning a foundation model.
- Claiming that an LLM judge is ground truth.
- Supporting every provider or every modality in the first release.
- Hiding provider-specific limitations behind an abstraction that cannot be debugged.

## 4. Target users and use cases

### Application developer

Sends a request once and receives a predictable response regardless of the selected provider. The developer can request a quality tier, latency target, budget ceiling, JSON schema, and idempotency key.

### Platform/operator

Inspects error rate, p95 latency, spend, cache hit rate, provider health, fallback frequency, and recent traces. The operator can disable a provider or change routing policy without redeploying application code.

### Evaluation owner

Runs a fixed dataset against one or more models and policy versions, reviews per-case results, and blocks promotion when quality or cost thresholds regress.

## 5. Architecture

```text
Client application
        |
        v
API Gateway / Load Balancer
        |
        v
FastAPI Gateway
  | auth, validation, idempotency, rate limits
  v
Policy + Router ---- Configuration store
  |                  (policies, models, budgets)
  +--> Semantic Cache (Redis)
  |
  +--> Provider Adapter(s)
  |       +--> Provider A
  |       +--> Provider B
  |       +--> Mock provider
  |
  +--> Response validator / redaction
  |
  +--> Event + usage recorder
             |              |
             v              v
        PostgreSQL       Metrics/traces
             |
             v
       Evaluation runner --> Dataset/artifact storage (S3)
```

The synchronous path should return the model response. Non-critical analytics and detailed events may be emitted asynchronously so observability does not materially increase request latency.

## 6. System components

### 6.1 API layer

FastAPI application with request IDs, authentication, schema validation, standardized errors, OpenAPI documentation, health endpoints, and versioned routes under `/v1`.

### 6.2 Provider adapters

Each adapter implements a common interface: capability discovery, request translation, invocation, response normalization, usage extraction, error classification, and model metadata. Provider SDKs must not leak into the domain layer.

### 6.3 Router and policy engine

Converts a request plus current provider health and model metadata into an ordered candidate list. The engine must be deterministic for the same inputs and produce an explainable decision record.

### 6.4 Reliability engine

Owns deadlines, retries, backoff, circuit state, fallback rules, idempotency, and classification of retryable versus permanent failures.

### 6.5 Cache

Stores normalized responses for eligible deterministic requests. Cache keys include model-policy version, prompt/input hash, schema hash, relevant parameters, tenant namespace, and application-defined cache version. Sensitive inputs must be opt-in and protected.

### 6.6 Usage and budget service

Calculates estimated and actual token/cost usage, aggregates by tenant/application/model/day, and rejects or downgrades requests that exceed configured limits.

### 6.7 Evaluation service

Runs datasets through selected models and policies, computes task-appropriate metrics, stores per-case evidence, and produces comparison reports.

### 6.8 Observability layer

Produces structured logs, Prometheus-compatible metrics, and OpenTelemetry traces. Correlation IDs connect the API request, provider attempts, cache operation, and persistence events.

## 7. API specification

### `POST /v1/generate`

Request fields: `input`, `model_policy`, `quality_tier`, `latency_budget_ms`, `max_cost_usd`, `temperature`, `max_output_tokens`, `cache_mode`, `metadata`, and `idempotency_key`.

Response fields: `request_id`, `output`, `provider`, `model`, `finish_reason`, `usage`, `estimated_cost_usd`, `latency_ms`, `cache_hit`, `fallback_used`, and `policy_version`.

### `POST /v1/extract`

Accepts `input`, a named or inline JSON Schema, routing constraints, and idempotency information. Returns validated JSON plus the same operational metadata as `/generate`. Invalid model output is retried within the deadline and then returned as a typed `OUTPUT_VALIDATION_FAILED` error.

### `POST /v1/evaluations/runs`

Starts an evaluation run for a dataset version, model list, and policy version. Returns `run_id` and status.

### `GET /v1/evaluations/runs/{run_id}`

Returns status, aggregate metrics, threshold results, and links/identifiers for per-case results.

### `GET /v1/metrics/summary`

Returns time-windowed request volume, success rate, p50/p95 latency, provider error rate, fallback rate, cache hit rate, token usage, and cost.

### `GET /health/live` and `/health/ready`

Liveness must not require external dependencies. Readiness checks required dependencies and reports degraded components without exposing secrets.

## 8. Data models

Use PostgreSQL for durable records and Redis for ephemeral state/cache.

Core tables/entities:

- `tenants`: tenant, plan, status, daily/monthly budget, rate limits.
- `model_profiles`: provider, model name, capabilities, context limit, input/output pricing, enabled status.
- `routing_policies`: policy name/version, candidate models, weights, constraints, fallback order.
- `requests`: request ID, tenant, endpoint, timestamps, status, policy version, cache result, redacted input hash.
- `attempts`: request ID, provider/model, start/end, outcome, retry number, error class, response validation result.
- `usage_events`: request/attempt IDs, input tokens, output tokens, estimated cost, pricing version.
- `evaluation_runs`: dataset version, policy/model versions, status, aggregate metrics, threshold outcome.
- `evaluation_cases`: run ID, case ID, expected output/reference, actual output or protected artifact pointer, scores, evaluator version.
- `audit_events`: actor, action, target, timestamp, before/after configuration hashes.

Never persist raw prompts or outputs by default. Support configurable redaction, hashing, encryption, retention, and explicit debug sampling.

## 9. Routing logic

Routing is a constrained ranking problem, not an opaque random choice.

1. Validate the request and derive a task class such as extraction, classification, summarization, or free-form generation.
2. Filter models by capability, context length, structured-output support, tenant allowlist, and budget.
3. Remove unhealthy providers whose circuit is open or whose remaining deadline is insufficient.
4. Estimate tokens and cost.
5. Rank candidates using configured weights for quality, cost, expected latency, and recent health.
6. Apply policy constraints such as `max_cost_usd` and `latency_budget_ms`.
7. Attempt candidates in order, recording the explanation and each outcome.
8. Retry only retryable failures, with a per-request deadline and attempt cap.
9. Validate the final response; retry or fall back on malformed structured output.

The initial policy should be deterministic and configuration-driven. A later adaptive policy may use historical evaluation quality and observed latency, but must remain bounded, explainable, and safe to roll back.

## 10. Reliability requirements

- Per-provider connect, read, and total-deadline timeouts.
- Maximum two retries per provider by default; exponential backoff with jitter.
- Retry only timeout, connection reset, 429, and selected 5xx errors.
- No retry for authentication, invalid request, policy, or schema errors.
- Circuit breaker with closed, open, and half-open states.
- Ordered fallback across providers and models.
- Idempotency keys to prevent duplicate billable requests.
- Graceful degradation: cached response, cheaper model, or typed unavailable response where policy permits.
- Bulkhead limits so one provider or tenant cannot exhaust all worker capacity.
- Explicit deadline propagation to every downstream call.
- Contract tests for every adapter and fault-injection tests for outages.

## 11. Caching requirements

Cache only requests marked eligible by policy. Normalize equivalent inputs, include all output-affecting parameters in the key, and use versioned namespaces. Store response, model/policy version, creation time, TTL, usage metadata, and a privacy classification.

Required behaviors: hit, miss, expired entry, bypass, explicit invalidation, namespace invalidation, and protection against cache stampedes. Measure hit rate and latency savings. A cache hit must be clearly identified to callers and operators.

## 12. Evaluation framework

Create a versioned dataset with representative cases, expected outputs, difficulty labels, and optional reference explanations. Include easy, ambiguous, malformed, long-context, and adversarial cases.

Metrics should match the task:

- extraction: schema validity, exact field accuracy, normalized field accuracy;
- classification: accuracy, precision, recall, F1 and confusion matrix;
- summarization/generation: rubric score, reference similarity where appropriate, and human review sample;
- operations: p50/p95 latency, success rate, fallback rate, cost per case.

Every run records dataset, code, evaluator, prompt, model, policy, and pricing versions. Add regression gates such as: schema validity ≥ 99%, task accuracy not below baseline by more than 2 percentage points, p95 within target, and cost within budget. Human review remains required for ambiguous or high-impact cases.

## 13. Observability

Required metrics include request count, success/error rate, p50/p95/p99 latency, provider latency, timeout count, retry count, fallback count, circuit state, cache hit/miss, token usage, estimated cost, validation failures, and evaluation scores.

Structured logs must include request ID, tenant ID (non-sensitive), endpoint, policy/model, attempt number, outcome, latency, and error class. Never log API keys or raw sensitive content. Traces should show gateway, cache, router, provider attempt, validator, and persistence spans.

Define alerts for elevated error rate, p95 latency, cost spikes, repeated validation failures, circuit openings, and budget exhaustion. Provide a runbook for each alert.

## 14. Security and privacy

- Store provider credentials in AWS Secrets Manager or an equivalent secret store.
- Authenticate clients with signed tokens or API keys; hash stored keys and support rotation/revocation.
- Enforce tenant isolation, request size limits, rate limits, and per-tenant budgets.
- Redact common PII from logs and evaluation artifacts; document limitations.
- Encrypt data in transit and at rest; restrict S3 and database access by least privilege.
- Validate schemas and reject prompt/configuration payloads above limits.
- Treat model output as untrusted data; never execute generated code or follow generated URLs automatically.
- Maintain audit events for policy and credential-related changes.
- Define retention and deletion procedures for prompts, outputs, traces, and evaluation artifacts.

## 15. Deployment and AWS design

Development runs with Docker Compose: gateway, PostgreSQL, Redis, mock provider, and observability services.

Recommended initial AWS deployment: containerized FastAPI service on ECS Fargate behind an Application Load Balancer; RDS PostgreSQL; ElastiCache Redis; S3 for evaluation artifacts; CloudWatch plus OpenTelemetry-compatible export; Secrets Manager; IAM roles; VPC private subnets; Terraform-managed infrastructure.

Keep the first production deployment small and reproducible. Prefer one service and clear boundaries before splitting into workers. Evaluation jobs may run as ECS tasks or a queue-backed worker after the synchronous system is stable.

## 16. CI/CD

On every pull request: formatting, linting, type checking, unit tests, adapter contract tests, API schema checks, dependency/security scan, and Terraform validation.

On merge: build an immutable Docker image, run integration tests, publish an artifact, deploy to a staging environment, run smoke tests, and require an evaluation gate before production promotion. Use Terraform plan review, environment-specific configuration, migration checks, and rollback to the prior image/policy version.

## 17. Testing strategy

- Unit tests for routing, cost estimation, key generation, redaction, retry classification, backoff, circuit transitions, and budget enforcement.
- API tests for validation, auth, idempotency, error contracts, and OpenAPI compatibility.
- Adapter contract tests using mocks and recorded sanitized fixtures.
- Integration tests with PostgreSQL and Redis containers.
- Fault-injection tests for timeout, 429, 5xx, malformed output, stale cache, and provider outage.
- Property tests for cache-key completeness and policy determinism.
- Load tests for concurrency, tail latency, and tenant isolation.
- Evaluation regression tests on every prompt/policy/model change.
- Security tests for secret leakage, authorization boundaries, oversized payloads, and log redaction.

## 18. Repository structure

```text
llm-reliability-gateway/
├── app/
│   ├── api/              # FastAPI routes and schemas
│   ├── domain/           # Provider-neutral models and policies
│   ├── routing/          # Candidate selection and explanations
│   ├── reliability/      # retries, deadlines, circuit breakers
│   ├── providers/        # provider adapters and mock provider
│   ├── cache/             # Redis cache and keying
│   ├── usage/             # tokens, pricing, budgets
│   ├── evaluation/        # runner, scorers, reports
│   ├── observability/     # logs, metrics, tracing
│   └── security/          # auth, redaction, limits
├── tests/                 # unit, integration, contract, fault, load
├── datasets/              # versioned sanitized evaluation data
├── migrations/
├── infra/terraform/
├── deploy/
├── docs/                  # architecture, runbooks, ADRs
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

## 19. Example flows

### Successful extraction

Client submits a ticket and schema. The gateway authenticates, derives `extraction`, checks Redis, selects the lowest-cost healthy model meeting the quality policy, calls it, validates JSON, records usage/cost, emits trace data, and returns the normalized result.

### Timeout and fallback

The preferred provider exceeds its deadline. The attempt is recorded as retryable; one bounded retry occurs if time remains. The circuit health score is updated, a secondary provider is called, and the response includes `fallback_used: true` without exposing internal secrets.

### Evaluation regression

A new routing policy is evaluated against dataset `v3`. Accuracy improves by 1%, but p95 latency and cost exceed thresholds. The run is marked failed, CI blocks promotion, and the prior policy remains active.

### Budget exhaustion

A tenant exceeds its daily budget. New requests are rejected with a typed error or routed to a configured low-cost fallback. The event is auditable and visible in metrics.

## 20. Milestones

### Milestone 1 — Foundation

Scaffold the service, configuration, provider interface, mock provider, Docker Compose, health endpoints, and basic `/v1/generate` route.

### Milestone 2 — Multi-provider routing

Add two real adapters, normalized responses, model profiles, deterministic policy routing, cost estimation, and routing explanations.

### Milestone 3 — Reliability

Add deadlines, retries, circuit breakers, fallback, idempotency, rate limits, and fault-injection tests.

### Milestone 4 — Cache and budgets

Add Redis semantic/exact cache, invalidation, stampede protection, token accounting, pricing, tenant budgets, and dashboards.

### Milestone 5 — Evaluation

Add versioned datasets, task scorers, evaluation runs, reports, regression thresholds, and CI evaluation gates.

### Milestone 6 — Production deployment

Add Terraform AWS infrastructure, secrets, staging deployment, telemetry, security controls, runbooks, load tests, and rollback procedure.

## 21. Acceptance criteria

The project is release-ready when:

1. A new client can integrate using documented APIs without provider-specific code.
2. At least two providers and a mock provider pass the same adapter contract suite.
3. Injected timeout, 429, 5xx, malformed-output, and outage scenarios produce bounded, explainable behavior.
4. Requests cannot exceed configured deadline, retry, rate, or budget limits.
5. Structured extraction never returns unvalidated JSON as a successful response.
6. Cache behavior is deterministic, privacy-aware, versioned, and measurable.
7. An operator can identify provider, policy, cost, latency, fallback, and error causes from one request ID.
8. Evaluation runs are reproducible and promotion gates fail on defined regressions.
9. Infrastructure can be created in a clean AWS account/environment from Terraform documentation.
10. CI runs all required checks and a documented rollback succeeds in staging.

## 22. Measurable success metrics

Track baseline versus gateway behavior using a fixed workload:

- ≥99% schema-valid responses for the extraction benchmark;
- ≥99% successful handling of injected transient failures through retry/fallback;
- p95 gateway overhead below 100 ms excluding provider latency;
- ≥20% cost reduction versus always using the strongest model on the benchmark;
- ≥25% cache hit rate on a repeated-request workload;
- 100% of billable requests linked to usage and pricing metadata;
- 100% of production requests traceable by request ID without raw sensitive payload logging;
- reproducible staging deployment from a clean checkout;
- zero secrets in repository, image, logs, or CI artifacts.

These are targets, not claims. Record the workload, date, configuration, and measurement method in the benchmark report.

## 23. Stretch goals

- Async batch evaluation and queue-backed workers.
- Canary routing and automatic policy rollback.
- Multi-region provider failover.
- Provider-score calibration from historical evaluation and SLO data.
- Prompt-injection and sensitive-data detection as optional policy stages.
- OpenAI-compatible `/v1/chat/completions` compatibility layer.
- Lightweight operator UI for traces, cost, and evaluation comparisons.
- Formal SLO/error-budget reporting.
- Kubernetes deployment after ECS is stable.

## 24. Suggested technology stack

**Language/runtime:** Python 3.12, FastAPI, Pydantic, asyncio.  
**Persistence:** PostgreSQL, SQLAlchemy, Alembic.  
**Cache/limits:** Redis.  
**Providers:** two provider SDKs behind custom adapters plus a deterministic mock.  
**Testing:** pytest, pytest-asyncio, Hypothesis, Testcontainers, Locust or k6.  
**Quality:** Ruff, mypy, pre-commit.  
**Observability:** OpenTelemetry, Prometheus-compatible metrics, Grafana/CloudWatch.  
**Packaging/deployment:** Docker, GitHub Actions, Terraform, ECS Fargate, RDS, ElastiCache, S3, Secrets Manager.

Avoid adopting LangChain or a large orchestration framework unless a specific requirement justifies it; implementing the gateway’s core abstractions directly makes the engineering decisions clearer and easier to defend.

## 25. Resume-relevant outcomes

Once verified with real measurements, the project can support bullets such as:

- Built a Python/FastAPI LLM reliability gateway with provider adapters, policy-based model routing, structured-output validation, retries, circuit breakers, and cross-provider fallback.
- Reduced benchmark inference cost by **X%** and p95 latency by **Y%** through quality-aware routing and Redis caching across **N** model configurations.
- Designed an evaluation and observability pipeline that tracked schema validity, quality, token usage, cost, latency, fallback rate, and regression thresholds across versioned datasets.
- Provisioned reproducible AWS infrastructure with Terraform, Docker, ECS Fargate, RDS PostgreSQL, ElastiCache Redis, S3, IAM, and Secrets Manager; automated validation and staged deployment in CI/CD.

Use numbers only after running and recording the benchmark. The strongest interview story is the tradeoff: what failed, which policy changed, how it was measured, and why the final design was chosen.

## 26. Definition of done

The repository includes a working local quickstart, API examples, architecture diagram, threat model, ADRs for routing/cache/reliability choices, seeded evaluation dataset, benchmark report, Terraform deployment guide, runbooks, test evidence, and a concise demo script. A reviewer can run the system, induce failures, inspect the resulting trace, reproduce an evaluation, and understand the measured tradeoffs without relying on undocumented knowledge.
