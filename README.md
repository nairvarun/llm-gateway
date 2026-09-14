# LLM Reliability Gateway

A planned Python gateway between applications and language-model providers,
focused on bounded failures, validated output, explainable routing, spend
accounting, and reproducible evaluation.

Status: specification and implementation plan only. There is no application
code, local service quickstart, cloud deployment, or measured benchmark yet.

## Start here

- [Scope and specification index](LLM-Reliability-Gateway-Spec.md)
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
openspec/
  config.yaml                   Planning context and artifact rules
  changes/
    build-llm-reliability-gateway/
      proposal.md               Why and capability scope
      design.md                 How, tradeoffs, and unresolved decisions
      specs/*/spec.md           Planned requirements and scenarios
      tasks.md                  Unchecked implementation milestones
    archive/                    Completed changes (currently empty)
  specs/                        Durable capabilities (currently empty)
docs/
  roadmap.md                    Delivery gates and benchmark methodology
  handoff-review.md             Provenance and reconciliation notes
  reference/original-handoff.md Unchanged historical specification
sources/                        Read-only synced references
```

Application, test, dataset, and infrastructure directories will be created when
their milestones are implemented; empty code scaffolds are intentionally absent.

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

The active change describes future behavior, not an implementation claim. Review
its design assumptions before applying a milestone. Use the OpenSpec apply skill
only when implementation is requested; verify before syncing/archiving. Keep
durable specs and the repository status consistent with the resulting work.

`sources/` is protected and contains a resume-writing reference, not service
source code. Any eventual resume summary must use verified engineering evidence.
