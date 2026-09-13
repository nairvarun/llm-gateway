# Handoff review

Reviewed and reorganized on September 13, 2026. This records the rationale for
the documentation refinement, not service implementation or benchmark evidence.

## Inputs and provenance

- Root `AGENTS.md` described the ChatGPT “Gul Resume” project and a resume that
  is not in this checkout. Its read-only `sources/` protection remains in force;
  its task context now describes this gateway repository.
- `LLM-Reliability-Gateway-Spec.md` was a 26-section informal build handoff. Its
  complete original content is preserved in
  [reference/original-handoff.md](reference/original-handoff.md). The root file
  now navigates refined scope and normative OpenSpec capability contracts.
- `sources/resume-guide.md` is synced resume-writing guidance, not application
  source or proof of candidate achievements. It has been read but not changed.
- Before reorganization, there was no OpenSpec root or application code. Existing
  inputs were untracked; no files have been committed as part of this task.

## Refinements

| Informal handoff issue | Refined treatment |
| --- | --- |
| Requirements, stack choices, examples, roadmap, and resume bullets in one spec | Capability requirements/scenarios, separate design/tasks, and a roadmap/evidence plan |
| Entire platform presented as a build specification without delivery gates | Six gated milestones; first delivery is an offline mock vertical slice |
| Semantic cache described using exact hashed identity | Exact caching is the baseline; semantic reuse requires a separate change, false-hit evaluation, and privacy design |
| Idempotency phrased as preventing duplicate billable requests | Scoped durable ownership/replay, conflict and uncertain states; no exactly-once upstream guarantee |
| Hard deadline/actual-spend claims with no recovery caveats | One monotonic deadline with measured tolerance; conservative estimated-liability reservations and unknown-usage holds |
| Cheap fallback on exhausted budget | Fallback must fit original quality and remaining accumulated allowance; exhausted budget blocks dispatch |
| Usage/analytics potentially asynchronous after success | Durable accounting/idempotency is critical path; optional telemetry is best effort |
| No raw-output retention versus cache/replay needs | Explicit short-lived encrypted content exceptions for opt-in cache and keyed replay |
| Vague output-validation retry | Same deadline/attempt/spend caps, locally validated schemas, truncation/refusal failure, documented precedence |
| Broad performance percentages without a workload | Aspirational targets with denominators, comparison conditions, and evidence requirements |
| Production-style AWS recommendations read as current infrastructure | Proposed authorized staging design; no paid calls, provisioning, or deployment performed |

Semantic reuse and online/debug production-content sampling are intentionally
gated rather than quietly assumed. The API, state/recovery defaults, and operator
CLI approach are proposed design choices to review before implementation.

## Authority and status

[The active proposal](../openspec/changes/build-llm-reliability-gateway/proposal.md)
declares seven capabilities. Their full specs define planned behavior; design
defines the approach; tasks define implementation progress. Historical examples
and resume guidance do not override them. `openspec/specs/` remains empty because
there is no verified implemented baseline to publish yet.

The proposal workflow produced planning artifacts only. OpenSpec validation
checks formatting/structure, not runtime correctness, feasibility of performance
targets, owner approval of defaults, or successful cloud deployment. Future
implementation must supply those checks and update the repository's status.
