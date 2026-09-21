# LLM Reliability Gateway

A Python gateway project between applications and language-model providers,
focused on bounded failures, validated output, explainable routing, spend
accounting, and reproducible evaluation.

Status: milestones 1–5 implemented offline. Authenticated mock generation,
schema-validated extraction, PostgreSQL evidence, two fixture-tested live-provider
adapters, deterministic versioned routing, bounded retry/fallback, shared Redis
admission/circuits, optional encrypted idempotency replay, atomic UTC spend
reservations, opt-in encrypted exact caching, synthetic evaluation/gates,
tenant-scoped summaries, and bounded telemetry/alerts are available. The HTTP
runtime remains mock-only. A small synthetic benchmark is published; live-model
quality/cost, cloud deployment, and staging release evidence remain future work.

## Run the offline gateway

See the [quickstart](docs/quickstart.md) for Docker/Lima prerequisites, API examples,
configuration, tests, and scope limitations. With `uv` and Docker Compose ready:

```sh
uv python install 3.12
uv sync --frozen
docker compose up --build -d --wait
uv run gateway seed-local
uv run python -m deploy.smoke
```

No paid provider credentials are required. The mock echoes JSON for extraction;
it is not a language model. Local client keys are generated into private ignored
files, never embedded in source/images. Initially downloading packages/images
requires internet access. Use the Lima alternative if Docker is not installed.

## Start here

- [Scope and specification index](LLM-Reliability-Gateway-Spec.md)
- [Foundation verification](docs/milestone-1-verification.md)
- [Routing verification](docs/milestone-2-verification.md)
- [Reliability verification](docs/milestone-3-verification.md)
- [Cache and budget verification](docs/milestone-4-verification.md)
- [Evaluation and observability verification](docs/milestone-5-verification.md)
- [Synthetic benchmark and raw evidence](docs/milestone-5-benchmark.md)
- [Ordered learning checklist](docs/learning-checklist.md)
- [Build proposal](openspec/changes/build-llm-reliability-gateway/proposal.md)
- [Architecture and decisions](openspec/changes/build-llm-reliability-gateway/design.md)
- [Milestone tasks](openspec/changes/build-llm-reliability-gateway/tasks.md)
- [Roadmap and measurement plan](docs/roadmap.md)
- [Handoff review and refinements](docs/handoff-review.md)
- [Agent instructions](AGENTS.md)

## Repository layout

```text
AGENTS.md                       Agent workflow and safety constraints
LLM-Reliability-Gateway-Spec.md  Scope and links to normative requirements
README.md                       Entry point and current state
pyproject.toml / uv.lock         Python package, checks, locked dependencies
app/                            API/domain/adapters/routing/security/cache/usage/evaluation/telemetry
tests/                          Unit/API/adapter and real-PostgreSQL checks
migrations/                     Forward-only foundation through evaluation/ingress migrations
datasets/                       Approved synthetic fixtures and threshold profile
deploy/                         Bounded startup, smoke, evaluation and benchmark helpers
Dockerfile / compose.yaml        Local service, PostgreSQL, and critical Redis
.github/workflows/ci.yml         Offline checks and container smoke
openspec/
  config.yaml                   Planning context and artifact rules
  changes/
    build-llm-reliability-gateway/
      proposal.md               Why and capability scope
      design.md                 How, tradeoffs, and unresolved decisions
      specs/*/spec.md           Planned requirements and scenarios
      tasks.md                  Milestone progress (later work remains unchecked)
    archive/                    Completed changes (currently empty)
  specs/                        Durable capabilities (currently empty)
docs/
  quickstart.md                 Runnable local setup/examples/checks
  milestone-1-verification.md    Verification evidence and implementation boundary
  milestone-2-verification.md    Offline adapter/routing evidence and boundaries
  milestone-3-verification.md    Offline reliability evidence and boundaries
  milestone-4-verification.md    Offline budget/cache evidence and boundaries
  milestone-5-verification.md    Offline evaluation/telemetry evidence and boundaries
  milestone-5-benchmark.md       Synthetic workload, targets versus measured results
  benchmark-evidence-v1.json     Sanitized per-case evidence and configuration
  provider-selection.md          Source-checked models, prices, and fixture provenance
  roadmap.md                    Delivery gates and benchmark methodology
  handoff-review.md             Provenance and reconciliation notes
  reference/original-handoff.md Unchanged historical specification
sources/                        Read-only synced references
```

Cloud infrastructure remains a later authorized milestone. Live-provider adapters
exist but are not connected for paid execution; synthetic evaluation is not a
live-model promotion approval.

## Planning workflow

OpenSpec was initialized using the local CLI (version 1.13.0) with `--tools none`:
the existing skills are available without adding generated editor integrations.
From the repository root:

```sh
openspec context --json
openspec list --json
openspec status --change build-llm-reliability-gateway
openspec validate build-llm-reliability-gateway --strict --no-interactive
```

The active change describes the full planned program; its checked tasks and
verification evidence identify the delivered foundation. Review
its design assumptions before applying a milestone. Use the OpenSpec apply skill
only when implementation is requested; verify before syncing/archiving. Keep
durable specs and the repository status consistent with the resulting work.

`sources/` is protected and contains a resume-writing reference, not service
source code. Any eventual resume summary must use verified engineering evidence.
