# Milestone 2 verification — offline adapters and routing

Verified locally on 20 September 2026. OpenSpec tasks 2.1–2.7 are implemented;
the full program remains active. No live provider credential, paid request,
cloud resource, quality benchmark, or production spend guarantee was used.

## Delivered slice

The exact-pinned OpenAI and Anthropic SDK adapters normalize provider results,
usage absence, refusal, truncation, and classified faults behind the common
contract. SDK automatic retries are disabled. Both adapters run against
hand-authored synthetic responses in in-memory transports; neither is wired to
the HTTP runtime. [Model selection and source provenance](provider-selection.md)
records the selected IDs, published prices/limits, mutable-alias caveat, and
fixture origin.

PostgreSQL now holds immutable validated policy/model/pricing versions plus an
audited active-policy pointer and provider-disable state. The router filters
task/schema/context/output/tenant/health/deadline/spend constraints and ranks
eligible candidates by fixed-bound weighted scores, then provider/model ID as
the stable tie-break. The mock has a conservative synthetic token bound. Live
token bounds are unavailable, so strict-budget routing fails closed; model
quality and latency scores are configuration estimates, not measured facts.

Each completed mock response carries sanitized candidate evidence and immutable
version IDs. It is also stored under the request ID, without raw prompt/output.
No-eligible requests store a failed rejection record before returning an error.
Provider enablement is read again immediately before dispatch; a late disable
records a `provider_disabled` exclusion without invoking the mock. Configuration
is read from PostgreSQL on every request, with no application cache; the design's
five-second maximum refresh target is met by immediate reads in the tested local
path, not by a measured distributed rollout guarantee.

## Checks executed

- `uv sync --frozen`: pinned SDKs installed without provider credentials.
- `uv lock --check` passed. `uv run ruff format --check app migrations tests deploy`:
  47 files already formatted. `uv run ruff check app migrations tests deploy`:
  passed. `uv run mypy`: no issues in 47 files. `uv run pytest`: **167 passed**,
  including real PostgreSQL integration tests; none skipped.
- Real PostgreSQL integration tests cover immutable versions, audited
  publish/activate/rollback/disable CLI, unauthorized mutation, invalid policy
  dependencies, retained in-flight snapshots, no-eligible evidence, and
  disablement after selection but before dispatch. Unit/property-style tests
  cover decimal arithmetic, unavailable bounds, exclusions, ranking and stable
  ties. The shared offline adapter contract exercises both SDKs and the mock.
- Local Lima/nerdctl image build, forward migration from the previous local
  `0001_foundation` database to `0002_routing_control`, existing-key reseeding,
  and authenticated generate/extract smoke passed. Readiness returned 200 with
  database healthy and optional Redis healthy. The database volume and private
  local key were retained.
- In the in-app browser, Swagger UI loaded, readiness “Try it out” returned
  HTTP 200, and the `GenerateResponse` schema visibly contained `routing`.
  The private API key was not entered into the browser.
- `openspec validate build-llm-reliability-gateway --strict --no-interactive`:
  passed (artifact structure only). Local Markdown relative links resolved;
  `git diff --check` passed; `sources/` and the original handoff had no changes.

## Boundaries and next gate

The HTTP service is still single-attempt mock-only. Live adapters have not been
tested against a real vendor endpoint and may need wire-shape review before an
authorized smoke. No retries/fallback, circuit control, shared admission,
idempotency, cache, atomic budget reservations, evaluation, telemetry pipeline,
benchmark, or staging deployment exists. Price estimates are decimal estimates,
not invoices; unknown usage is not silently zero. No live dispatch can be enabled
until milestone 3 reliability and milestone 4 spend/cache/privacy gates pass,
and a separate owner authorization supplies a spending ceiling.

The next implementation milestone is reliability (tasks 3.1–3.8). Do not sync or
archive the program-level OpenSpec change at this point.
