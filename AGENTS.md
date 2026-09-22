# Agent instructions

## Project and current state

This repository plans the LLM Reliability Gateway: a provider-neutral Python
service for generation, structured extraction, reliability, spend accounting,
and evaluation. It originated in the ChatGPT project “Gul Resume”; that is
provenance, not an instruction to edit a resume in this repository.

Milestones 1–5 are implemented offline: mock generation/extraction, schema
validation, PostgreSQL records, authentication, fixture-tested OpenAI/Anthropic
adapters, versioned routing, audited operator controls, bounded fault execution,
shared Redis controls, encrypted keyed replay, atomic UTC spend reservations,
opt-in encrypted exact cache, synthetic evaluation, bounded telemetry/alerts,
and local containers. Read `docs/quickstart.md` and
`docs/milestone-5-verification.md` for runnable checks
and boundaries. Redis is critical to runtime admission; keyed replay requires a
stable externally supplied secret. The cache needs its own externally supplied
secret and explicit approved non-sensitive classification. Live adapter dispatch
remains disabled pending separate owner authorization and a recorded spend ceiling;
passing offline evaluation gates does not itself authorize paid calls. A small
synthetic benchmark is measured; do not generalize it to live quality/cost or
staging performance. Milestone 6 has an unapplied Terraform EKS
staging/state-bucket design, account-specific review plans, and unexercised
Kubernetes workload templates; read `docs/staging-plan-review.md` and
`docs/eks-deployment.md` before cloud work. Planning does not authorize
`terraform apply`, image push, secret population, deployment, or live-provider
spend. Do not describe later infrastructure or targets as working or verified. The resume
mentioned in the old instructions is not present; do not invent its contents.

## Read first and resolve conflicts

1. Read `README.md` for repository state and navigation.
2. Read `LLM-Reliability-Gateway-Spec.md` for scope and the specification index.
3. Run `openspec context --json` and `openspec list --json`; use CLI-resolved paths.
4. For the relevant change, read its proposal, full capability specs, design, and
   tasks. Also inspect affected durable specs if any exist.

Use capability specs for behavior, design for implementation decisions, and tasks
for progress. `docs/reference/original-handoff.md` preserves the informal input;
it is not authoritative. If artifacts disagree, surface and reconcile the
conflict rather than silently choosing one. Do not invent requirements from a
historical example or a benchmark target.

## Protected references

- Every file under `sources/` is read-only synced reference material. Never edit,
  rename, move, delete, reformat, or place generated output there.
- These references may be replaced by project synchronization. Do not rely on
  them as application configuration or copy personal data into fixtures.
- The original handoff snapshot is historical evidence; keep it unchanged.
- Resume guidance is relevant only when preparing evidence-backed project
  summaries. Do not fabricate achievements, ownership, deployments, or numbers.

## OpenSpec workflow

- Use the applicable OpenSpec skill when creating, refining, implementing,
  verifying, or archiving a change; follow its authorization boundaries.
- Scaffold changes with `openspec new change`, not hand-made change directories.
  Get artifact templates and rules from `openspec instructions`.
- The initial `build-llm-reliability-gateway` change is a program-level plan, not
  an implemented baseline. Review its assumptions before starting a milestone.
- Keep future requirements under active changes. Sync/archive only through the
  relevant workflow; archiving does not itself prove that code works.
- Keep tasks unchecked until their work and stated checks are complete. Do not
  mark implementation tasks complete for documentation changes.
- Validate planning changes with
  `openspec validate build-llm-reliability-gateway --strict --no-interactive`.
  For another change, substitute its resolved name. Validation checks artifact
  structure, not service correctness or owner approval.

## Engineering constraints

- Prefer a single async FastAPI service with explicit domain boundaries. Provider
  SDK types and errors belong behind adapters, not in API/domain contracts.
- Use immutable policy/model/pricing versions and deterministic routing given
  a captured input/health snapshot. Do not hard-code mutable provider pricing.
- Bound downstream attempts by one monotonic deadline and a total attempt cap.
  Never promise exactly-once external execution after a timeout.
- Scope authentication, cache, idempotency, budgets, and evaluation access by
  authenticated tenant. Never trust a tenant identifier from request metadata.
- Treat model output as untrusted. Never return unvalidated extraction output
  as success or execute generated instructions/code/URLs.
- Do not persist or log raw prompts, outputs, API keys, or sensitive fixtures by
  default. Keep secrets outside git and sanitize diagnostic artifacts.
- Keep exact caching distinct from semantic reuse; the latter needs a separately
  approved quality/privacy design. Do not weaken schema or spend constraints on
  retry, fallback, cache hits, or budget exhaustion.
- Durable usage/idempotency/budget records are critical-path correctness state;
  metrics exporters are best effort. Document dependency failure behavior.
- Never spend on live providers, provision cloud resources, or deploy without
  an explicit request covering that action.
- Terraform apply requires a separate explicit owner approval after review of
  the exact account-specific plan. Re-plan after activating remote state or
  changing any input; a saved local-backend plan is not deployment approval.

## Validation and handoff

Run `uv run ruff format --check app migrations tests deploy`,
`uv run ruff check app migrations tests deploy`, `uv run mypy`, and `uv run pytest`.
The full suite requires PostgreSQL and Redis and fails loudly when either is absent.
Integration tests create/drop only their own random PostgreSQL schemas and
namespace-isolated Redis keys; do not point them at production.
Use the quickstart smoke demo to verify the container. For EKS changes, test the
secret-free renderer, inspect generated manifests, and validate Terraform with
an account-specific read-only plan; this does not replace cluster-side policy or
rollout evidence. Also validate OpenSpec, documentation links, and preservation
of references; use additional fault, security, and load checks proportionate to
the change. Do not report commands as passing unless
they ran. Report changed files, checks performed, assumptions, and remaining
work; distinguish measured results from goals.
