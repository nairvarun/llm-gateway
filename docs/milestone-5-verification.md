# Milestone 5 verification — offline evaluation and observability

Verified locally on 21 September 2026 with synthetic mock traffic. OpenSpec
tasks 5.1–5.9 are covered by implementation and the checks below. This does
not establish live-model quality, production spend, cloud deployment, or a
staging SLO. The active program change remains open.

## Delivered boundary

- Strict versioned synthetic datasets and approved threshold profile have
  canonical hashes and reject malformed/private-looking fixtures. Runs, cases,
  audited generation review, and approved baseline identities are durable and
  tenant-scoped. The API enqueues 202; a separate explicit worker claims one
  case at a time. On interrupted ownership, uncertain cases are not silently
  redispatched. Evaluation uses normal gateway admission/accounting with cache
  bypass and a distinct traffic marker.
- Extraction validity/field accuracy, classification precision/recall/F1, and
  pending human generation review preserve failure denominators. Reports pin
  dataset, code, policy, model, pricing, sampling, prompt and scorer provenance
  and disclose mutable upstream revision limitations. Versioned gates compare
  baseline, quality, latency, and cost; missing evidence blocks rather than
  activating a policy. The local mock does not meet the task-quality profile.
- `/v1/metrics/summary` bounds windows and result volume by authenticated
  tenant/application, separates evaluation traffic from application requests,
  and distinguishes attempts, replays, cache hits, tokens, and fresh estimated
  spend. Bounded Prometheus metrics, sanitized structured events, sampled
  OpenTelemetry spans, and optional exporter failure state never replace the
  durable request/attempt records. `/metrics` requires operator credentials.
- [Alert rules and response](operations/alerts.md) cover failure, latency,
  spend, validation, circuit, exporter, and budget signals with scripted
  incident fixtures. CI requires the full PostgreSQL/Redis suite, OpenAPI
  compatibility, failing-evidence gates, alert/benchmark checks, dependency
  audit, secret scan, strict OpenSpec validation, and a container evaluation
  demo. Local execution of these commands does not prove remote Actions ran.

## Evidence

| Check | Result |
| --- | --- |
| Ruff format/lint, mypy, full PostgreSQL/Redis pytest suite, lockfile check | Passed; 269 tests, no required integration skip |
| `uv audit --locked` | Passed; 67 packages, no known vulnerability/adverse status |
| `openspec validate ... --strict --no-interactive` | Valid active change |
| Local Lima/nerdctl image, migrations through `0009_ingress_scope`, readiness, generate/extract smoke | Passed; PostgreSQL and Redis healthy |
| Separate worker and `deploy.evaluation_demo` | 7/7 synthetic cases completed; gate `blocked` with `missing_approved_baseline` |
| Browser Swagger regression | New run/gate/summary paths and readable examples visible; unauthenticated summary returned 401 with request ID; prior generate/extract container smoke passed |
| Offline benchmark | [Raw sanitized case evidence](benchmark-evidence-v1.json) reaggregates in `tests/test_benchmark.py`; [targets versus results](milestone-5-benchmark.md) published |
| Compatibility, gate, alert, exporter, scope/privacy and document-link tests | Passed within the 269-test suite |
| Local redacted gitleaks staged-content scan and protected-reference diff | No leaks found; `sources/` and historical handoff untouched |

The browser was not given a private API key. Terminal/container demonstrations
used a generated local synthetic tenant key; no paid provider credentials,
cloud resources, or live model calls were used. Remote CI results must be
checked separately after push.

## Remaining gates

The demo lacks an approved baseline and human generation review, and its mock
classification and extraction scores are below the chosen profile. Missing
review/evidence remains a blocker by design. The benchmark's small sequential
offline sample cannot justify cost savings, live quality, throughput, or a
production latency SLO. Provider dispatch stays mock-only pending explicit
owner authorization and a recorded spending ceiling. Milestone 6 requires
separate account/region/resource authorization and reviewed staging plans;
no deployment or rollback has been exercised.
