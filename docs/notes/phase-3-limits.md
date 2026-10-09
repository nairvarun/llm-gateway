# Phase 3: rate limits and budgets

**In two sentences.** A token bucket per key allows `rpm` requests per minute with bursts up to
`rpm`, and answers 429 with `Retry-After` when empty. A monthly budget is checked before each call,
which makes it a soft cap: concurrent requests can overspend it.

## What I learned

- **A token bucket is two numbers per key.** Tokens and the time they were last updated; refill is
  computed lazily on the next request, so no background timer is needed.
- **No lock needed in asyncio, as long as nothing awaits.** `acquire()` reads and writes the
  bucket without an `await` in between, so no other coroutine can interleave. The Redis version
  in K2 needs a Lua script to get the same atomicity across replicas.
- **Check-then-act is a race.** `test_budget_is_a_soft_cap_under_concurrency` shows three
  concurrent requests all passing a budget that only one should have. A hard cap would need
  reservations (hold an estimated cost, settle later). Spec D8 accepts the soft cap.
- **Where the limiter sits is a choice.** It runs before the cache, so cache hits still count.
  The limit protects the gateway, not only the providers.

## Metric

`gateway_rate_limited_total` and `gateway_budget_exceeded_total`.
