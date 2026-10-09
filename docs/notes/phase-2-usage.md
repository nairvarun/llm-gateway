# Phase 2: usage and cost tracking

**In two sentences.** Every authenticated request writes exactly one usage row when it finishes,
with tokens from the provider or, for a stream cut short, an estimate that is flagged. The write
is shielded so a client disconnect cannot drop it, and it is idempotent on `request_id`.

## What I learned

- **Accounting under partial failure is the hard part.** The happy path is easy; the interesting
  rows are the cancelled stream (status 499, estimated tokens) and the upstream that died mid-way
  (status 502 or 504, still billed for what was sent).
- **Order of `finally` blocks matters.** Route's relay sets `ctx.usage` from the final chunk; the
  usage stage reads it. Because wrappers close innermost first, usage always sees final state.
- **Idempotent writes are cheap insurance.** `ON CONFLICT(request_id) DO NOTHING` means a write
  that runs twice cannot double-bill.
- **Estimates are allowed if they are labeled.** Four characters per token is rough, so every
  estimated row says so (`usage_estimated = 1`) and is counted in a metric.

## Metric

`gateway_cost_usd_total{alias, provider}` and `gateway_usage_estimated_total`.
