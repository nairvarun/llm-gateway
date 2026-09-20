# Offline quickstart (milestones 1–3)

This runs the offline service: authenticated generation, locally validated
extraction, a deterministic mock, versioned routing, bounded retries, shared
Redis controls, optional keyed replay, and durable PostgreSQL evidence. No paid
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
to original evidence. A later retention job will purge expired protected bytes;
expiry already prevents replay. See [reliability verification](milestone-3-verification.md).

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
no exact cache, atomic spend reservation, evaluation runner,
metrics/tracing pipeline, or cloud deployment. Cache modes other than bypass
return `INVALID_REQUEST`; keyed requests without a stable replay key fail closed.
Deadline/cancellation/recovery controls have offline fault evidence, not a
production network/billing guarantee. Redis is correctness-critical for new
dispatch and readiness, not an optional optimization.

Raw prompts/outputs are not stored or logged by the application. Usage is linked
to request/attempt/pricing records before success is returned. A failed terminal
write leaves dispatch intent unresolved and returns a non-retryable error;
uncertain keyed state cannot be redispatched automatically. Crash recovery
records an unknown attempt and conservative upper liability; full budget holds
and reconciliation belong to milestone 4. This is an offline reliability demo,
not a production-ready gateway or a benchmark achievement.
