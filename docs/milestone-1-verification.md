# Milestone 1 verification

Verified locally on 13 September 2026. Only tasks 1.1–1.9 of
`build-llm-reliability-gateway` are delivered. The full OpenSpec change remains
active; milestones 2–6 are not implemented or archived as completed work.

## Delivered slice

The Python 3.12/FastAPI service authenticates hashed synthetic client keys,
derives tenant/application identity, and executes one deterministic mock attempt
for generation or extraction. Extraction uses bounded local JSON Schema
validation, including tenant-scoped named immutable versions. PostgreSQL records
request/attempt dispatch intent before invocation and terminal usage before a
successful response. No raw input/output or plaintext key is stored in those
records.

The local CLI seeds private credentials, inspects authorized metadata, and lets
an operator revoke keys with an audit event. Liveness is dependency-independent;
readiness treats PostgreSQL/migrations/mock configuration as critical and Redis
as optional. Docker/Compose assets and an offline GitHub Actions workflow are
included. Follow [the quickstart](quickstart.md) for exact commands.

## Checks executed

- `uv sync --frozen` installed the locked package into a newly created temporary
  virtual environment, using uv 0.12.13 and Python 3.12.14.
- `uv run ruff format --check app migrations tests deploy`: 37 files formatted.
- `uv run ruff check app migrations tests deploy`: passed.
- `uv run mypy`: no issues in 37 source files.
- `uv run pytest`: **84 passed**, including real PostgreSQL integration tests;
  none skipped. Every integration fixture applies migration `0001_foundation`
  into its own new random schema and removes only that test schema afterward.
- `openspec validate build-llm-reliability-gateway --strict --no-interactive`:
  passed. This is artifact validation, not evidence of the future platform.
- The container image was built and launched with nerdctl through the existing
  Lima instance, PostgreSQL 16 and Redis 7. Both the container-internal liveness
  probe and host readiness probe returned 200. Readiness reported database
  `healthy` and Redis `healthy_optional` without a URL or credential.
- `uv run python -m deploy.smoke` passed authenticated generation and named-schema
  extraction against the container, with correlated response headers/request IDs
  and zero mock cost. The startup wait tolerates connection resets/disconnects;
  regression tests cover transient failures and visible deadline exhaustion.
- Reference files and the original handoff snapshot remain unchanged.

Docker itself was not installed on the verification machine: the container path
was exercised with Lima/nerdctl, not a claimed Docker or remote GitHub Actions run.
The workflow's Docker Compose build/smoke step is configured but has not been
executed remotely. Dependency/image downloads used network access; mock execution
required no real provider credentials, paid calls, or cloud resources.

After verification, the Compose demo was left running on the default loopback
ports (API 8000, PostgreSQL 55432, Redis 56379), with a matching private
`.local/client-key`. The temporary standalone test container was removed; its
database volume was retained, and its key preserved as `.local/integration-key`.
No reference/project data was deleted. Use the quickstart's stop command to
release the running containers while retaining the demo database.

## Task evidence

| Task | Implementation | Verification |
| --- | --- | --- |
| 1.1 Package and checks | `pyproject.toml`, `uv.lock`, `.python-version` | Fresh environment install; package/format/lint/type/full-suite checks |
| 1.2 Provider contracts | `app/domain/models.py`, `app/providers/mock.py` | `test_provider_contract.py`: success, classified faults, refusal, truncation, malformed output, missing usage, synthetic output bound |
| 1.3 Persistence | Frozen Alembic migration; `app/persistence/` | `test_postgres.py`: clean migrations, immutable versions, unique usage events, idempotent terminal recording |
| 1.4 Authentication | Key hashes, tenant/application binding, audited operator revocation | API/PostgreSQL tests: missing/invalid/revoked keys, disabled tenants, operator denial, cross-tenant evidence/schema isolation; CLI key files are private/non-overwriting |
| 1.5 API schemas | `app/api/schemas.py`, request boundary, sanitized errors | `test_api.py`: OpenAPI, enums/bounds, chunked size limits, invalid JSON, schema conflicts and unsupported modes with no mock invocation |
| 1.6 Extraction validation | `app/api/schema_validation.py`, immutable named schemas | Schema/API tests: remote refs, cycles, unsupported keywords, size/depth, duplicate keys, nonfinite/overflowing numbers, invalid/refused/truncated output |
| 1.7 Execution records | `app/service.py`, transactional store | Real PostgreSQL/API tests: correlated success/failure/usage, privacy, injected pre-dispatch write failure prevents invocation; post-dispatch failure never claims unrecorded success |
| 1.8 Health/local runtime | `app/main.py`, Compose, non-root image/startup | API tests for critical/optional outages; actual image build and generate/extract/liveness/readiness smoke |
| 1.9 CI/quickstart | `.github/workflows/ci.yml`, `deploy/smoke.py`, quickstart | Fresh locked environment and local container demo; startup-wait regression tests; no remote CI result claimed |

## Boundaries and residual risk

This is a foundation, not a production reliability or billing guarantee. Model,
policy and pricing versions are immutable mock fixtures; prices are zero and
token counts are synthetic. No live adapters, candidate ranking, retry/fallback,
circuits, shared admission, idempotency replay, cache, spend reservations,
evaluation runner, telemetry pipeline, benchmark, or staging deployment exists.
Non-bypass cache modes and supplied idempotency keys are explicitly rejected.

A failed terminal write leaves durable dispatch intent unresolved and returns a
non-retryable dependency error rather than success. Crash/uncertain-state recovery
and distributed deadline/cancellation guarantees remain later work. Redis does
not yet own critical controls, so its failure is optional only in this milestone.
Local bootstrap/operator credentials and the Compose password are development
facilities, not a production provisioning or secret-rotation system.

The next planned milestone is offline multi-provider adapter/routing evidence.
It was not started by this implementation request. Do not sync/archive this whole
program-level change or claim later requirements have passed.
