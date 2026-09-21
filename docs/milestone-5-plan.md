# Milestone 5 implementation plan — offline evaluation and observability

The capability contract is `evaluation-observability`; the checklist is section 5
of the active OpenSpec change. No capability requirement needs revision. Implement
offline evidence before connecting any live provider or promotion target.

1. Define small, synthetic, immutable dataset manifests and strict loader checks.
   Cover extraction, classification, and generation with easy, ambiguous,
   malformed, long-context, and adversarial cases. Keep expected labels and
   outputs free of private data; pin canonical content hashes.
2. Migrate durable tenant-scoped dataset/run/case/baseline/profile records.
   Accept an authorized version-complete run as 202, expose lifecycle by ID,
   and process it only in an explicitly launched worker. Claim one case at a
   time with transactional ownership; interrupted cases become uncertain and
   cannot silently redispatch. Reuse normal gateway admission/budget execution
   with cache bypass and a distinct evaluation request marker.
3. Score outcomes by task, retaining failures in the denominator. Publish
   per-case sanitized evidence and aggregate validity/accuracy/F1/rubric review
   with human-review status, then compare an approved baseline and immutable
   threshold profile without changing the active policy on gate failure.
4. Add bounded telemetry and tenant-scoped window summaries from durable
   request/attempt/usage records, explicitly separating evaluation, replay, and
   cache traffic. Exporter loss is best effort and visible; request correlation
   remains durable. Document alert windows and scripted incident checks.
5. Make the offline evaluation, compatibility, dependency, and secret checks
   mandatory in CI. Run the deterministic benchmark and regression demo, record
   environment/configuration/raw sanitized evidence, then run quality commands,
   PostgreSQL/Redis integration, local browser, and regression checks. Update
   truthful status docs and mark tasks only after their checks pass. Commit and
   push this phase before beginning staging work.

No benchmark target is an achieved result until the exact workload and measured
numbers are published. Owner approval is required for any paid run or promotion
profile outside this synthetic offline fixture.
