# Offline quickstart (milestones 1–5)

This runs the offline service: authenticated generation, locally validated
extraction, a deterministic mock, versioned routing, bounded retries, shared
Redis controls, optional keyed replay, atomic spend reservations, opt-in exact
cache, synthetic evaluation/summary/telemetry, and durable PostgreSQL evidence. No paid
provider/AWS account is needed. Internet access is needed initially to download
Python packages and container images; provider invocations themselves are offline.

## Prerequisites

- `uv` and Python 3.12 (uv can install the interpreter).
- Docker with Compose, or an existing Lima instance with nerdctl Compose.
- Free loopback ports 8000 (API), 55432 (PostgreSQL), and 56379 (critical Redis).

The database password in Compose is explicitly local-development-only. Ports
bind to loopback; do not expose this environment to a network or treat it as
production security. Synthetic tenant credentials are randomly generated and
stored privately outside git; no real provider keys belong here.

## Start with Docker Compose

From the repository root:

```sh
uv python install 3.12
uv sync --frozen
docker compose up --build -d --wait
uv run gateway seed-local
uv run python -m deploy.smoke
```

Container startup waits for PostgreSQL, applies migrations, then starts the API.
The seed command creates a synthetic tenant, immutable free mock configuration versions,
the `demo-count` schema, and a mode-0600 `.local/client-key`. A repeat invocation
verifies the existing key without replacing it or printing it, and adds missing
milestone 2 mock registry rows when upgrading an older local database. Readiness is 503
until migrations, mock configuration, and Redis are available; liveness does not
probe storage. An absent or failed Redis control store rejects new execution.

The smoke demo reports successful generate/extract request IDs without printing
credentials. Open [API documentation](http://127.0.0.1:8000/docs) for published
types/defaults and use `X-API-Key` authentication. Keep the local key private.

## Lima alternative

If a nerdctl-enabled Lima instance already exists, replace Docker commands with:

```sh
limactl start default --tty=false
limactl shell default nerdctl compose up --build -d
uv run gateway wait-database --timeout 30
uv run gateway seed-local
uv run python -m deploy.smoke
```

Lima forwards loopback ports to the host. nerdctl may ignore Compose dependency
conditions; the gateway's own bounded startup wait handles database ordering.
Use the applicable instance name rather than creating an unrelated VM. On this
machine the container image and smoke demo were verified through existing Lima.

To use another PostgreSQL port, set both Compose's guest environment and the
host client's connection URL, for example:

```sh
limactl shell default env GATEWAY_POSTGRES_PORT=55433 nerdctl compose up --build -d
export GATEWAY_DATABASE_URL='postgresql+asyncpg://gateway:local-development-only@127.0.0.1:55433/gateway'
uv run gateway wait-database --timeout 30
uv run gateway seed-local --key-file .local/container-key
GATEWAY_SMOKE_KEY_FILE=.local/container-key uv run python -m deploy.smoke
```

Do not reuse a private key file from a different/cleared database: seed refuses
to overwrite an existing invalid/revoked key. Choose a new file or restore the
matching local database. Docker supports the same port environment variables on
the host. `GATEWAY_HTTP_PORT`/`GATEWAY_SMOKE_URL` configure the API port/client URL;
`GATEWAY_REDIS_PORT` controls the critical Redis host port.

## Run the API directly on the host

Start just the local dependencies, then migrate/seed before starting Uvicorn:

```sh
docker compose up -d postgres redis
uv run gateway wait-database --timeout 30
uv run alembic upgrade head
uv run gateway seed-local
export GATEWAY_REDIS_URL='redis://127.0.0.1:56379'
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

For Lima, substitute `limactl shell default nerdctl compose up -d postgres redis`.
Avoid starting host and container gateways on the same API port simultaneously.

## API examples and evidence

`POST /v1/generate` accepts `{"input":"synthetic demo"}` and returns normalized
text/metadata. `POST /v1/extract` accepts:

```json
{
  "input": "{\"count\": 2}",
  "schema_name": "demo-count",
  "schema_version": "v1"
}
```

Alternatively use `json_schema` with an inline object instead of name/version.
The mock echoes JSON input for extraction; it does not infer fields from prose.
Validation failures return typed errors and never successful unvalidated JSON.
USD values use decimal strings. Default mock cost is zero and token counts are synthetic
(one token per four Unicode code points), not real model or invoice measurements.
The response's `routing` object shows the pinned policy/model/pricing versions,
ranked candidates, score estimates, and non-sensitive exclusion reasons. A
`NO_ELIGIBLE_MODEL` response is also stored under its request ID without invoking
a provider.

Every ingress has a server-generated `X-Request-ID` matching `request_id`. Inspect
durable metadata using an ID from the smoke demo:

```sh
uv run gateway inspect-request REQUEST_ID
```

Use `--key-file .local/container-key` when following the alternate-port example.
Tenant credentials can inspect only their tenant's records. A trusted local
operator bootstrap and revocation path is available:

```sh
uv run gateway seed-local --operator --key-file .local/operator-key
uv run gateway revoke-key CREDENTIAL_ID --key-file .local/operator-key
```

Operator bootstrap is a local development command, not a public registration API.
The operator can publish validated immutable policy/model/pricing JSON, activate
or roll back a policy, and disable or re-enable a registered provider:

For a local policy experiment, put this JSON in `policy.json` and choose a new
version string; it reuses the immutable seeded mock model and pricing:

```json
{
  "candidates": [{"name": "mock-text-v1", "version": "v2", "order": 0}],
  "weights": {"quality": "1", "affordability": "1", "latency": "1", "health": "1"},
  "affordability_reference_usd": "1",
  "minimum_deadline_ms": 0,
  "attempt_limit": 1,
  "fallback_enabled": false
}
```

```sh
uv run gateway publish-config policy mock-policy v3 policy.json --key-file .local/operator-key
uv run gateway activate-policy mock-policy v3 --key-file .local/operator-key
uv run gateway disable-provider mock --key-file .local/operator-key
uv run gateway enable-provider mock --key-file .local/operator-key
uv run gateway rollback-policy mock-policy v2 --key-file .local/operator-key
```

Publishing does not activate a version. Activation checks referenced model and
pricing rows in the same transaction; each mutation is audited. Configuration
is read from PostgreSQL for each request and provider enablement is rechecked
before mock dispatch (no application cache; maximum refresh target five seconds).
A disabled mock yields `NO_ELIGIBLE_MODEL` without a new mock call. See the
[routing verification](milestone-2-verification.md) and
[provider matrix](provider-selection.md) for payload shape and limits.

Policies publish `attempt_limit` (1–4) and `fallback_enabled`. The seeded v2
policy retains one attempt; to exercise retries, publish and activate a new
validated policy with `attempt_limit` up to 4. At most two retries per candidate
and four provider invocations total can occur, all within one deadline and the
original request ceiling. The HTTP path still dispatches only the offline mock.
Redis enforces shared tenant/provider rate and concurrency limits and circuits;
readiness exposes sanitized mock circuit state/count. A Redis outage fails closed
for new execution, not merely as an optional cache degradation.

Keyed requests need `GATEWAY_REPLAY_ENCRYPTION_KEY`: a stable, externally held,
base64-encoded 32-byte random key. Omit it to disable keyed execution explicitly
with a 503 response; never commit or log it. Supply it through your secret store
or local shell environment before starting the container/host process. Do not
rotate it while 24-hour replay records may still be used: changing it makes
their identity/content inaccessible and could allow a new execution for the
same literal key. Same-tenant endpoint/key/fingerprint repeats replay an encrypted
terminal result under a new ingress ID with `original_request_id`; conflicts
return 409, in-progress owners return 409 plus `Retry-After`, and uncertain
execution remains blocked. Replay reports zero fresh provider usage and points
to original evidence. The operator retention job purges expired protected bytes;
expiry already prevents replay. See [reliability verification](milestone-3-verification.md).

## Spend and exact cache

The local synthetic tenant starts with a USD 1 per-request ceiling, USD 10 UTC
daily allowance, and USD 100 UTC monthly allowance. These are local defaults,
not approved production tenant limits. PostgreSQL atomically reserves an upper
estimate before every attempt; retries and fallback share the original request
ceiling. Known usage releases confirmed unused allowance. Unknown usage retains
its hold until an audited operator recovery conservatively charges it. Overruns
are recorded and disable the provider pending review. These controls bound
admitted **estimated liability**, not a provider invoice. The free mock has
zero-priced default entries; nonzero synthetic pricing is tested offline.

Authenticated clients can read only their own current UTC allowance at
`GET /v1/spend`. The local CLI can show the same view; an operator credential
can choose another tenant for investigation:

```sh
uv run gateway spend-summary
uv run gateway spend-summary --tenant-id TENANT_UUID --key-file .local/operator-key
uv run gateway recover-unknown-attempt ATTEMPT_UUID --key-file .local/operator-key
```

Exact cache is opt-in. Supply a stable external `GATEWAY_CACHE_ENCRYPTION_KEY`
containing a base64-encoded 32-byte random key before startup; never commit or
log it. The local synthetic tenant is approved for synthetic fixtures only;
other tenants default to unapproved. A request must set both
`"cache_mode":"read_write"` (or `"read_only"`) and
`"cache_classification":"approved_non_sensitive"`, with temperature zero.
Sensitive/unclassified or nondeterministic requests bypass the cache and report
`cache_status: "ineligible"`. Classification is a caller attestation under an
operator-approved tenant; the gateway does not inspect prose to prove it is
non-sensitive. Do not opt in production personal data. Missing/failed optional
cache degrades to uncached execution only while critical database and Redis
admission controls remain healthy. Cache entries are encrypted, exact-keyed by
tenant/application/schema/parameters/versions, and capped at one hour.
Successful hits report zero fresh provider usage/cost and a source request ID.

Operators can change cache approval or invalidate an exact identity or whole
application namespace. The exact identity hash appears in the response's
`routing.cache_key_hash` for opted-in eligible traffic; it is not the raw input.

```sh
uv run gateway approve-cache TENANT_UUID --key-file .local/operator-key
uv run gateway revoke-cache TENANT_UUID --key-file .local/operator-key
uv run gateway invalidate-cache-exact TENANT_UUID APP_ID CACHE_KEY_HASH --key-file .local/operator-key
uv run gateway invalidate-cache-namespace TENANT_UUID APP_ID --key-file .local/operator-key
```

The operator commands are audited in PostgreSQL. Invalidation advances durable
generations; old in-flight cache writes cannot populate the newly active
generation. Single-flight is fenced and deadline-bounded, not exactly-once
external execution. Read-only mode never writes on a miss.

Run `uv run gateway purge-retention --key-file .local/operator-key` on a trusted
daily schedule for physical deletion of expired replay records and old
operational metadata. Configure `GATEWAY_REPLAY_RETENTION_HOURS` (at most 24),
`GATEWAY_CACHE_TTL_SECONDS` (at most 3600), and
`GATEWAY_METADATA_RETENTION_DAYS` (default 30) within their validated bounds.
Redis cache entries expire by TTL. Unresolved budget holds and the active UTC
month bucket remain until safe reconciliation; tenant credentials, immutable
configuration, and invalidation generations are not deleted by this job.
Database backups and Redis snapshots may retain deleted ciphertext or metadata
until their separate backup lifecycle expires; this local repo does not configure
that lifecycle. Do not claim production erasure from the purge command alone.
See [milestone 4 evidence](milestone-4-verification.md).

## Synthetic evaluation and observability

The checked-in `synthetic-gateway@v1` dataset contains only synthetic cases.
The API queues a run with `POST /v1/evaluations/runs`, returns 202, and exposes
tenant-scoped run/case status and a fail-closed comparison at
`GET /v1/evaluations/runs/{run_id}/gate`. The worker is a separate explicitly
launched process, not an API background task. With the local container running:

```sh
uv run python -m deploy.evaluation_demo enqueue
docker compose exec -T gateway gateway evaluate-worker --max-cases 100
uv run python -m deploy.evaluation_demo verify RUN_ID_FROM_ENQUEUE
```

For Lima replace `docker compose exec -T gateway` with
`limactl shell default nerdctl exec llm-gateway-foundation-gateway-1`.
The verify step requires all seven cases to complete and a `blocked` gate with
`missing_approved_baseline`; it does not promote a policy. The approved
threshold profile additionally requires human review and task quality. An
interrupted claimed case is marked uncertain rather than blindly dispatched
again. Operator-only `gateway review-generation` and
`gateway approve-eval-baseline` require a trusted key and sufficient evidence;
do not approve the deliberately failing demo to make a gate pass.

`GET /v1/metrics/summary` returns bounded UTC windows for the authenticated
tenant/application and separates application from evaluation traffic. The
operator-only `/metrics` endpoint has bounded labels. Optional OpenTelemetry
export failure does not turn valid work into an error; durable request IDs remain
available. See [alert rules/runbooks](operations/alerts.md) and
[evaluation/observability evidence](milestone-5-verification.md).
The [synthetic benchmark](milestone-5-benchmark.md) publishes raw sanitized
evidence and limitations; no live-model quality or cost saving is established.

Request evidence includes status/identity/policy/routing/error metadata, never raw input
or output. Keys are verification hashes in PostgreSQL. Input hashes use a keyed
HMAC; set a private `GATEWAY_INPUT_HASH_KEY` for stable hashes across restarts,
otherwise an ephemeral key is generated for each process.

## Tests and quality checks

With PostgreSQL and Redis running:

```sh
uv run ruff format --check app migrations tests deploy
uv run ruff check app migrations tests deploy
uv run mypy
uv run pytest
uv audit --locked
openspec validate build-llm-reliability-gateway --strict --no-interactive
```

The full suite fails visibly when PostgreSQL or Redis is unavailable; required integration
tests are not silently skipped. `TEST_DATABASE_URL` overrides its connection.
`TEST_REDIS_URL` overrides the local Redis integration endpoint.
Each integration test migrates a newly created random schema and deletes only
that schema; it never truncates/drops the developer's public tables. The database
user therefore needs local schema-creation privileges.

For pure unit/API-double checks without PostgreSQL:

```sh
uv run pytest -m 'not integration'
```

This smaller suite is useful during development but does not replace PostgreSQL
verification. GitHub Actions runs quality/full tests, builds the container, and
smoke-tests a separate Compose database. The workflow has been added; no remote
Actions run is claimed by local verification.

## Fault demo and stopping

`GATEWAY_MOCK_SCENARIO` is operator-controlled, not a client metadata field.
Supported faults include `timeout`, `connection`, `rate_limit`, `server`,
`credential`, `invalid_request`, `malformed`, `schema_invalid`, `refusal`,
`truncation`, and `missing_usage`. Set it when starting/recreating the gateway.
The seeded policy makes one attempt; a versioned policy can enable bounded retry
and fallback. The smoke script expects `success`, so use the API/tests to inspect
faults and restore the default afterward.

Stop containers while retaining local data:

```sh
docker compose stop
```

For Lima: `limactl shell default nerdctl compose stop`. Do not remove the database
volume unless you intentionally want to discard local tenants/evidence; its loss
also invalidates private key files. Data-destructive automatic Alembic downgrade
is deliberately disabled; future schema changes need forward migrations.

## Current boundary

OpenAI and Anthropic adapters and deterministic ranking have offline contract
tests, but the HTTP runtime cannot dispatch them. Their conservative token
bounds remain unavailable, so strict-budget selection excludes them. There is
no evaluation runner, metrics/tracing pipeline, or cloud deployment. Cache
reuse is exact and conditional on explicit approval and a stable cache key;
keyed requests without a stable replay key fail closed.
Deadline/cancellation/recovery controls have offline fault evidence, not a
production network/billing guarantee. Redis is correctness-critical for new
dispatch and readiness, not an optional optimization.

Raw prompts/outputs are not stored or logged by the application. Usage is linked
to request/attempt/pricing records before success is returned. A failed terminal
write leaves dispatch intent unresolved and returns a non-retryable error;
uncertain keyed state cannot be redispatched automatically. Crash recovery
retains an unknown attempt and conservative upper liability until audited
reconciliation. This is an offline reliability demo,
not a production-ready gateway or a benchmark achievement.
