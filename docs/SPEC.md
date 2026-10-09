# LLM Gateway: Spec

*Python, FastAPI, Kubernetes. v0.2*

## 1. Purpose and principles

A small OpenAI-compatible gateway that sits between clients and LLM providers. It adds virtual API keys, usage and cost tracking, rate limits, spend budgets, timeouts, retries, fallback, a circuit breaker, metrics, tracing, and caching, and it runs on Kubernetes. The project exists for learning, so its author must be able to explain every part of it.

**Principles**

1. **Simplicity is the anchor, not the absence of features.** Many features, each one small and isolated.
2. **One feature, one file.** A feature may touch its stage file, one config model, one line in pipeline assembly, and its tests. If it needs more, the design is wrong.
3. **The two-sentence test.** Build a feature only if it teaches a concept not yet touched and can be explained in two sentences afterward.
4. **Boring tech.** Few dependencies, no frameworks beyond the web layer.
5. **Every feature ships with a test, a metric, and a short note** in `docs/notes/` on what was learned.
6. **Size budget.** Core under about 2,000 lines excluding tests. Going over is a signal to simplify, not to relax the budget. If something must give, tracing goes first.
7. **Write down the trade-off.** If a behavior is deliberately imperfect (a soft budget, a per-replica breaker), the spec says so and the note explains why.

## 2. Scope

**In scope**

- OpenAI-compatible chat completions, streaming and non-streaming
- Two providers: OpenAI (near passthrough) and Anthropic (translated)
- Virtual API keys with per-key model lists, rate limits, and budgets
- Token and cost tracking, including for streams and cancelled streams
- Rate limiting and monthly spend budgets
- Timeouts, retries, fallback, circuit breaker
- Prometheus metrics, structured logs, OpenTelemetry tracing
- Exact-match caching
- Kubernetes deployment with probes and graceful shutdown, then scale-out with an HPA

**Out of scope:** see section 12.

## 3. Architecture

```
client
  └─ observe            (request_id, logs, metrics, trace span — wraps everything)
      └─ auth           (who is calling; 401/403 here)
          └─ usage      (one usage row per authenticated request — wraps the rest)
              └─ rate_limit   (429 + Retry-After)
                  └─ budget         (402-style refusal before spending money)
                      └─ cache            (non-streaming, temperature == 0)
                          └─ route        (target selection, timeouts, retries,
                                           fallback, circuit breaker, provider call)
```

**Stack**

| Concern | Choice | Why |
| --- | --- | --- |
| Web | FastAPI + uvicorn | Async, streaming responses, minimal ceremony |
| Upstream HTTP | httpx (async) | Streaming support, explicit timeouts |
| Config | pydantic-settings + YAML | Typed, validated at startup |
| Storage | SQLite via aiosqlite | Zero ops; swapped in K2 behind an interface |
| Metrics | prometheus-client | Standard on Kubernetes |
| Tracing | opentelemetry-sdk + OTLP exporter | Standard; off unless an endpoint is configured |
| Tests | pytest, pytest-asyncio, respx | Mock upstreams without network |
| Tooling | uv, ruff | Fast, simple |

### 3.1 Pipeline contract

Stages are plain async callables. Do not use Starlette's `BaseHTTPMiddleware`, which interferes with streaming. The chain lives inside the route handler.

```python
@dataclass
class RequestContext:
    request_id: str
    body: ChatRequest
    key: VirtualKey | None = None
    target: Target | None = None          # provider + model actually used
    attempts: int = 0
    started_at: float = field(default_factory=time.monotonic)
    first_byte_at: float | None = None
    usage: Usage | None = None
    usage_estimated: bool = False
    cache_hit: bool = False
    status: int | None = None

class Stage(Protocol):
    async def __call__(self, ctx: RequestContext, call_next: Next) -> GatewayResponse: ...
```

**Stage order is fixed in code, not in config.** Order carries meaning (auth must come before rate limiting, and usage must wrap everything after auth), so letting config reorder stages is a footgun. Config may only enable or disable an optional stage (`cache`, `budget`). Pipeline assembly is one list in `app.py`; adding a feature adds one line to it.

### 3.2 Streaming and the wrapping pattern

For streaming, `GatewayResponse` carries an async iterator of chunks. A stage that needs the final result (usage, logging, cache write, metrics) wraps that iterator and acts in a `finally` block when it is exhausted, fails, or is cancelled. This is the main technique of the project.

Rules for wrappers:

- A wrapper never buffers. It yields each chunk as soon as it arrives.
- Work done in `finally` after a cancellation (the usage write) runs under `asyncio.shield` so a second cancellation cannot drop it. A test proves the row is written when the client disconnects mid-stream.
- **Errors after the first byte** cannot change the HTTP status, which is already 200. The gateway sends one final SSE event containing an OpenAI-shaped `error` object, then closes the stream. The usage row records the error status.

### 3.3 Provider contract

```python
class Provider(Protocol):
    name: str
    supports_tools: bool
    async def complete(self, req: ChatRequest, model: str) -> ChatResponse: ...
    def stream(self, req: ChatRequest, model: str) -> AsyncIterator[ChatChunk]: ...
```

The canonical internal format is the OpenAI schema.

- **OpenAI adapter:** near passthrough. For streams it always sets `stream_options.include_usage = true` upstream so usage is reported. If the client did not ask for usage, the adapter strips the usage-only final chunk before it reaches the client.
- **Anthropic adapter:** translates system prompt placement, the required `max_tokens` (filled from config when absent), stop reasons, and SSE event types (`message_start`, `content_block_delta`, `message_delta`, `message_stop`) into OpenAI chunks. Usage comes from `message_start` (input) and `message_delta` (output). It translates nothing beyond that.

**Tool calls** are passed through only to providers with `supports_tools = true` (OpenAI in v1). Route selection filters out targets that cannot serve the request. If no target is left, the gateway returns 400 with a clear message. This makes fallback across providers safe without normalizing tool calls.

## 4. API surface

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/chat/completions` | Main endpoint, `stream: true` supported |
| `GET /v1/models` | Aliases the calling key may use |
| `GET /healthz` | Liveness: the process is up. Never checks dependencies. |
| `GET /readyz` | Readiness: config loaded, store reachable, not draining |
| `GET /metrics` | Prometheus |
| `POST /admin/keys` | Create a virtual key. The plaintext key is returned once, at creation. |
| `GET /admin/keys/{id}/usage` | Usage summary for a key (this month, by alias) |
| `DELETE /admin/keys/{id}` | Revoke a key (sets `revoked_at`) |

Errors use the OpenAI error shape (`{"error": {"message", "type", "code"}}`) so existing SDKs behave correctly. Every response carries an `x-request-id` header. An incoming `x-request-id` is reused if it is well-formed.

**Status codes the gateway itself produces:** 400 (bad request, no capable target), 401 (unknown or revoked key), 403 (model not allowed for key, or virtual key on `/admin`), 429 (rate limit, with `Retry-After`), 402 (monthly budget exhausted), 502 (all upstream attempts failed), 503 (draining, or every target's breaker is open), 504 (upstream timeout).

## 5. Configuration

One YAML file, mounted from a ConfigMap. Secrets come from environment variables, never from the file. The whole file is validated at startup, and the process refuses to start on any error.

```yaml
providers:
  openai:
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
  anthropic:
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    default_max_tokens: 1024

models:
  fast:
    targets:
      - {provider: openai, model: gpt-4o-mini}
      - {provider: anthropic, model: claude-haiku-latest}
  smart:
    targets:
      - {provider: anthropic, model: claude-sonnet-latest}

pricing:            # USD per 1M tokens, keyed by provider model ID
  gpt-4o-mini: {input: 0.15, output: 0.60}
  claude-haiku-latest: {input: 0.80, output: 4.00}
  claude-sonnet-latest: {input: 3.00, output: 15.00}

stages:
  cache: {enabled: true, max_entries: 1000, ttl_s: 300}
  budget: {enabled: true}

timeouts:
  connect_s: 3
  first_byte_s: 20     # request sent -> first chunk (or full body for non-streaming)
  idle_s: 30           # max gap between chunks once streaming
  total_s: 300         # hard ceiling for any single request

retries:
  max_attempts: 3      # across retries and fallbacks combined
  backoff_base_s: 0.25 # full jitter: sleep uniform(0, base * 2**n)

breaker:
  failure_threshold: 5
  open_s: 30

shutdown:
  drain_s: 330         # >= timeouts.total_s
```

**Startup validation**

- Every target references a defined provider.
- Every target model has a pricing entry. No silent `null` cost.
- Every provider's `api_key_env` is set.
- `shutdown.drain_s >= timeouts.total_s`.

Client-facing model names are aliases. The first target is primary and later targets are fallbacks. Model IDs and prices above are placeholders; verify them when building.

## 6. Data model

```sql
CREATE TABLE api_keys (
  id TEXT PRIMARY KEY,
  hash TEXT UNIQUE NOT NULL,       -- sha256 of the key, never the key
  name TEXT NOT NULL,
  allowed_models TEXT,             -- JSON list of aliases, null = all
  rpm_limit INTEGER,               -- null = unlimited
  monthly_budget_usd REAL,         -- null = unlimited
  created_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE usage (
  id INTEGER PRIMARY KEY,
  request_id TEXT UNIQUE NOT NULL, -- one row per request
  key_id TEXT NOT NULL REFERENCES api_keys(id),
  ts TEXT NOT NULL,                -- UTC ISO-8601
  model_alias TEXT NOT NULL,
  provider TEXT,                   -- final provider; null if none was called
  model TEXT,                      -- final provider model ID
  attempts INTEGER NOT NULL DEFAULT 0,
  input_tokens INTEGER,
  output_tokens INTEGER,
  usage_estimated INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  latency_ms INTEGER,
  ttft_ms INTEGER,
  status INTEGER NOT NULL,
  cache_hit INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX usage_key_ts ON usage (key_id, ts);
```

**Usage row rules**

- One row per **authenticated** request, including 429s, 402s, upstream failures, and cancelled streams. Failed auth is not recorded in the table, because there is no key to attribute it to. It is counted in metrics and logs.
- Retries and fallbacks belong to the same row. `attempts` counts them, and `provider`/`model` record the final target. Tokens and cost come from the attempt that produced the response. Attempts that failed before any output are treated as free.
- Cache hits record `cache_hit = 1` and `cost_usd = 0`.
- Rows are written inline when the response finishes. There is no background queue.

**Store interface.** All access goes through `Store`. Nothing else imports a database driver.

```python
class Store(Protocol):
    async def get_key_by_hash(self, hash: str) -> VirtualKey | None: ...
    async def create_key(self, name: str, allowed_models: list[str] | None,
                         rpm_limit: int | None, monthly_budget_usd: float | None) -> tuple[VirtualKey, str]: ...
    async def revoke_key(self, key_id: str) -> bool: ...
    async def record_usage(self, row: UsageRow) -> None: ...
    async def spend_this_month(self, key_id: str) -> float: ...
    async def usage_summary(self, key_id: str) -> UsageSummary: ...
    async def ping(self) -> bool: ...
```

**Keys** look like `gw-` followed by 32 random URL-safe bytes. A plain SHA-256 is fine because the keys are high-entropy; slow hashes exist for low-entropy passwords. This trade-off belongs in the Phase 1 note.

## 7. Features and acceptance criteria

A phase is finished only when its tests pass, its metric exists, and its note is written.

### Phase 0: Core proxy

- Non-streaming and streaming completions work against both providers through one endpoint.
- Streaming is passed through without buffering. A test proves the first chunk reaches the client before the upstream finishes.
- Client disconnect cancels the upstream request. A test proves the upstream connection is closed.
- `include_usage` is injected upstream for OpenAI streams and stripped from the client stream when the client did not ask for it.
- Requests with `tools` only go to targets that support tools; otherwise 400.
- *Concepts:* async generators, SSE, backpressure, cancellation, format translation.

### Phase 1: Virtual keys and auth

- The bearer key is hashed and looked up; unknown or revoked keys get 401.
- Keys can be restricted to a list of aliases; other aliases get 403.
- `/admin/*` requires `ADMIN_TOKEN`, compared with `hmac.compare_digest`. A virtual key calling `/admin/*` gets 403, covered by a test.
- *Concepts:* hashing secrets, key lifecycle, authentication versus authorization, timing attacks.

### Phase 2: Usage and cost tracking

- Every authenticated request writes exactly one `usage` row, following the rules in section 6.
- Streaming usage comes from the provider's final usage data. If the stream is cut short (client disconnect, upstream error, idle timeout), tokens are estimated (about 4 characters per token for output; input from the request) and `usage_estimated = 1`.
- The usage write survives cancellation (`asyncio.shield`), and a test proves it.
- Cost is computed from the pricing table.
- *Concepts:* stream wrapping, accounting under partial failure, cancellation semantics.

### Phase 3: Rate limits and budgets

- Token bucket per key for requests per minute; 429 with `Retry-After` in whole seconds.
- Monthly spend cap per key, checked before the call using `spend_this_month`; 402 when exhausted.
- **The budget is a soft cap.** Concurrent requests can each pass the check before any of them records its cost, so a key can overspend by up to (concurrency × one request's cost). This is accepted and documented, not fixed. The note explains why a hard cap would need reservations.
- Rate limiting runs before the cache, so cache hits count against RPM. This is deliberate: the limit protects the gateway, not just the providers.
- *Concepts:* token bucket, where limiter state lives, check-then-act races.

### Phase 4: Resilience

- **Four timeouts:** connect, first byte, idle between chunks, and a total ceiling. An idle or total timeout mid-stream ends the stream with an SSE error event (section 3.2).
- **Retries** use full-jitter exponential backoff on 429, 5xx, connect errors, and first-byte timeouts, **only before the first byte reaches the client**. An upstream `Retry-After` is honored if it fits within the remaining total timeout.
- **Fallback** moves to the next capable target in the alias. Retries and fallbacks share `retries.max_attempts`.
- **Circuit breaker** per provider (closed → open after N consecutive failures → half-open after `open_s` with one trial request). Open breakers are skipped during target selection. If every target is open, the gateway returns 503.
- *Concepts:* retry safety and idempotency, why streams cannot be retried midway, failure isolation, retry storms.

### Phase 5: Observability

- **Metrics:** request count, latency histogram, TTFT histogram, tokens, cost, errors by provider and status, in-flight requests, retries, cache hits, breaker state, auth failures.
- **Label rules:** labels are limited to alias, provider, model, and status class. Key IDs are never labels; per-key data lives in the usage table. The note explains cardinality.
- **Logs:** JSON, one line per request, with `request_id`. Prompts and completions are never logged by default.
- **Tracing:** one OpenTelemetry span per request, with a child span per provider attempt. Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- Ships with a Grafana dashboard JSON and two alert rules (error rate, p95 TTFT).
- *Concepts:* RED metrics for streaming workloads, cardinality control, spans versus metrics.

### Phase 6: Caching

- Exact-match cache for **non-streaming** requests whose body explicitly sets `temperature: 0`. Requests that omit it use the provider default (1.0) and are not cached.
- The cache key is the SHA-256 of the canonical JSON body (sorted keys, `stream` and `user` removed) plus the alias **and the key ID**. Scoping to the key gives up some hit rate in exchange for never serving one tenant a response generated for another.
- In-memory LRU with TTL. `x-gateway-cache: bypass` skips both read and write.
- Responses carry `x-gateway-cache: hit|miss|bypass`.
- *Concepts:* cache keys, what is and is not safe to cache, why `temperature: 0` is still not truly deterministic.

### Stretch (only after everything above is done)

Semantic cache, request and response guardrail hook, a third provider, per-key model aliases, tool-call translation for Anthropic.

## 8. Kubernetes deployment

**Packaging:** a multi-stage Dockerfile on a slim Python base, non-root user, read-only root filesystem (the SQLite volume and an `emptyDir` for `/tmp` are the only writable mounts). Manifests are managed with Kustomize, with a base plus `k1` and `k2` overlays.

### 8.1 Objects

| Object | K1 | K2 |
| --- | --- | --- |
| Deployment | 1 replica, `strategy: Recreate` | ≥2 replicas, RollingUpdate |
| Service | ✓ | ✓ |
| ConfigMap (YAML config) | ✓ | ✓ |
| Secret (provider keys, admin token) | ✓ | ✓ |
| PVC (SQLite, ReadWriteOnce) | ✓ | removed |
| Scrape annotations or ServiceMonitor | ✓ | ✓ |
| NetworkPolicy | ✓ | ✓ (plus Redis/Postgres) |
| Ingress or Gateway route (`/v1` only) | ✓ | ✓ |
| PodDisruptionBudget (`maxUnavailable: 1`) | — | ✓ |
| HPA | — | ✓ |

**Why K1 uses Recreate.** A ReadWriteOnce volume attaches to one node at a time. With RollingUpdate, a new pod scheduled on another node cannot mount the volume, never becomes ready, and the old pod is never removed, so the deploy hangs. Recreate accepts a short outage per deploy. Removing that outage is one of the reasons for K2.

**Why there is no PDB or HPA in K1.** A PDB on a single replica either blocks node drains or protects nothing, and an HPA cannot scale a pod that owns a ReadWriteOnce volume.

**NetworkPolicy.** Standard NetworkPolicy matches IPs and CIDR ranges, not hostnames, and provider IPs change. The policy allows egress to DNS and TCP 443 only, and denies everything else. Hostname-level egress would need a CNI with FQDN policies (e.g. Cilium) or an egress proxy, which is out of scope. The Phase 5 note records this limitation.

### 8.2 Operational requirements

- `livenessProbe` on `/healthz`, `readinessProbe` on `/readyz`.
- Resource requests and limits come from load-test results, not guesses.
- Config reload happens through a rollout (checksum annotation on the pod template), not hot reload.

**Graceful shutdown, in the order Kubernetes actually runs it:**

1. The pod is marked terminating, and endpoint removal begins in parallel.
2. The `preStop` hook runs `sleep 10`. This gives endpoints and the ingress time to stop sending traffic, and it is what actually prevents dropped requests.
3. SIGTERM is delivered. The app sets `draining = true`, so `/readyz` fails as a safety net, and new requests get 503.
4. Uvicorn stops accepting connections and waits for in-flight requests and streams, bounded by `--timeout-graceful-shutdown` = `shutdown.drain_s`.
5. `terminationGracePeriodSeconds` ≥ preStop sleep + `drain_s` + a small margin, so the kubelet never sends SIGKILL to a stream that is still running.

### 8.3 Admin endpoints

`/admin/*` is served by the same app and port as the API, and is protected in three ways:

- A separate `ADMIN_TOKEN` from a Secret, never a virtual key, compared in constant time.
- No Ingress or Gateway route for `/admin`; it is reached in-cluster or with `kubectl port-forward`.
- A test asserting that virtual keys get 403 on `/admin/*`.

### 8.4 State and scaling in two steps

1. **K1: single replica.** SQLite on a PVC, in-memory limiter, cache, and breaker. Deliberately simple and correct.
2. **K2: scale out.** Rate limiting moves to Redis (atomic token bucket in a Lua script), and keys and usage move to Postgres, behind the existing `Limiter` and `Store` interfaces. Then the deployment runs multiple replicas with RollingUpdate, a PDB, and an HPA on CPU (or in-flight requests through a custom-metrics adapter, as a stretch). **The pipeline code does not change; that is the test of the interface design.**
   - The cache and circuit breaker stay per replica. The cache hit rate drops by roughly the replica count, and each replica learns about provider failures on its own. Both are accepted trade-offs and get one paragraph in the K2 note.

### 8.5 Cluster baseline (M1.5)

The first target is a fresh cluster. Phases 0 and 1 run locally in Docker and do not need it, but the first gateway deploy does. Before it, the cluster needs:

- A default StorageClass, for the SQLite PVC.
- An ingress controller or Gateway API implementation.
- metrics-server, which the K2 HPA depends on.
- A Prometheus stack. Use the Prometheus Operator with a `ServiceMonitor`, or scrape annotations until one exists.

## 9. Testing

- **Unit:** each stage tested alone with a fake `call_next`.
- **Adapter:** recorded provider fixtures replayed with respx, including streams and the injected and stripped usage chunk.
- **Integration:** the full app against a fake upstream that can delay, fail, truncate, stall mid-stream, and stream slowly.
- **Required named tests** (each one maps to a claim in this spec):
  - first chunk arrives before the upstream finishes
  - client disconnect closes the upstream connection
  - client disconnect still writes a usage row with `usage_estimated = 1`
  - idle timeout mid-stream ends with an SSE error event
  - no retry happens after the first byte has been sent
  - virtual key on `/admin/*` gets 403
  - `tools` request skips targets without tool support
  - config with a target missing pricing fails to start
- **Load:** k6 or Locust with many concurrent streams, run in the cluster. Record p95 TTFT, memory per stream, and (in K2) behavior during a rolling deploy.
- **Chaos:** kill the upstream mid-stream, kill a pod mid-stream, and observe the results against the shutdown sequence above.

## 10. Repository layout

```
gateway/
  app.py            # FastAPI app, routes, pipeline assembly (one list)
  config.py         # pydantic models + startup validation
  context.py        # RequestContext, GatewayResponse
  schemas.py        # OpenAI-shaped request/response models
  errors.py         # OpenAI error shape, status mapping
  providers/        # base.py, openai.py, anthropic.py
  stages/           # observe.py, auth.py, usage.py, rate_limit.py,
                    # budget.py, cache.py, route.py
  resilience/       # timeouts.py, retry.py, breaker.py (used by route)
  store/            # base.py, sqlite.py  (K2: postgres.py)
  limiter/          # base.py, memory.py  (K2: redis.py)
  metrics.py
tests/
deploy/             # Dockerfile, kustomize base + k1/k2 overlays, dashboards, alerts
docs/
  SPEC.md
  notes/            # one short note per phase
```

## 11. Decisions

| # | Decision | Reason |
| --- | --- | --- |
| D1 | Usage rows are written inline; no background queue. | Simpler, and correct. Revisit only if load tests show it is the bottleneck. |
| D2 | Admin endpoints share the API port, protected as in 8.3. | One process and one port; not routing `/admin` publicly does the isolation. |
| D3 | The first target is a fresh cluster, so M1.5 exists. | The baseline is real work and should be tracked. |
| D4 | Stage order is fixed in code; config only enables or disables stages. | Order carries meaning; making it configurable invites bugs. |
| D5 | One usage row per authenticated request; failed auth goes to metrics only. | No key means nothing to attribute the row to; avoids unauthenticated table growth. |
| D6 | Retries and fallbacks share one row and one attempt budget. | One request means one bill line and one bound on upstream load. |
| D7 | Tools are passthrough to capable providers only, enforced by target filtering. | Safe fallback without building tool-call translation. |
| D8 | The budget is a soft cap. | A hard cap needs reservations; the race is the lesson. |
| D9 | The cache is scoped per key and requires an explicit `temperature: 0`. | Correctness and tenant isolation over hit rate. |
| D10 | K1 uses Recreate and has no PDB or HPA. | A ReadWriteOnce volume makes rolling updates and autoscaling unsafe. |
| D11 | NetworkPolicy is limited to DNS + 443. | Vanilla NetworkPolicy cannot match hostnames. |
| D12 | Size budget is 2,000 lines; tracing is the first thing cut. | Realistic for this feature set; the principle still holds. |

## 12. Not building (do not reopen)

Admin UI, orgs and multi-tenancy, plugin or hook system, SSO, embeddings and image endpoints, tool-call translation across providers (stretch only), more than three providers, prompt management, hot config reload, hard (reservation-based) budgets, hostname-based egress policy, semantic caching before Phase 6 is done.

## 13. Milestones

| Milestone | Contents | Done when |
| --- | --- | --- |
| M1 | Phases 0 and 1, running in Docker locally | Both providers stream through Docker with virtual keys |
| M1.5 | Cluster baseline (8.5) | A test pod can use a PVC, be reached through ingress, and be scraped |
| M2 | Phases 2 and 3, first deploy (K1) | Usage and limits work in-cluster; Recreate deploy observed |
| M3 | Phases 4 and 5, dashboards, load test, chaos tests | Dashboards show a chaos run; resource limits set from the load test |
| M4 | Phase 6, then K2 scale-out | Multiple replicas behind the HPA, rolling deploy with no dropped streams, pipeline code unchanged |
