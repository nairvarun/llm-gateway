# LLM Reliability Gateway

A Python gateway project between applications and language-model providers,
focused on bounded failures, validated output, explainable routing, spend
accounting, and reproducible evaluation.

Status: milestones 1–2 implemented offline. Authenticated mock generation,
schema-validated extraction, PostgreSQL evidence, two fixture-tested live-provider
adapters, deterministic versioned routing, and audited operator controls are
available. The HTTP runtime remains mock-only. There are no paid provider calls,
cloud deployment, or measured benchmark results.

## Run the foundation

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
app/                            API/domain/adapters/routing/security/PostgreSQL implementation
tests/                          Unit/API/adapter and real-PostgreSQL checks
migrations/                     Forward-only foundation and routing migrations
deploy/                         Bounded startup and offline smoke helpers
Dockerfile / compose.yaml        Local service and PostgreSQL/optional Redis
.github/workflows/ci.yml         Offline foundation checks and container smoke
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
  provider-selection.md          Source-checked models, prices, and fixture provenance
  roadmap.md                    Delivery gates and benchmark methodology
  handoff-review.md             Provenance and reconciliation notes
  reference/original-handoff.md Unchanged historical specification
sources/                        Read-only synced references
```

Dataset and infrastructure directories will be created when their milestones are
implemented. Live-provider adapters exist but are not connected for paid execution;
cache/reliability controls, evaluation, and cloud resources remain later work.

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
