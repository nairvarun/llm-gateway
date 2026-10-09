# Phase 4: resilience

**In two sentences.** Four timeouts (connect, first byte, idle, total) bound every request, and
failures before the first byte are retried with jittered backoff across the alias's targets. A
circuit breaker per provider skips a provider after five straight failures and probes it with
one trial request after 30 seconds.

## What I learned

- **Streams cannot be retried midway.** Once a chunk has reached the client, a retry would send a
  second, different answer glued onto the first. The relay ends with an SSE error instead.
- **One deadline beats four independent timeouts.** Every wait uses
  `min(its own timeout, time left before the deadline)`, so retries plus slow chunks can never
  outlive `total_s`.
- **Not every error is the provider's fault.** A 400 means the provider is healthy and the request
  was bad: no retry, and the breaker records a *success*. A 429 is neutral: retry after its
  `Retry-After`, but tell the breaker nothing.
- **Half-open needs care.** The first breaker draft could leak its single trial slot when the
  trial ended in a 429 or a client cancel, leaving the provider skipped forever. `release()` frees
  the slot on neutral outcomes, and `can_try()` (pure) is separate from `begin()` (claims the slot)
  so building the candidate list does not claim slots for targets that are never called.
- **Rotation is a simple fallback policy.** Attempt *n* uses `available[n % len(available)]`:
  one target means plain retries, two mean primary, fallback, primary.

## Metric

`gateway_breaker_state{provider}` and `gateway_upstream_attempts_total{provider, outcome}`.
