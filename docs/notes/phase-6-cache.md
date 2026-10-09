# Phase 6: caching

**In two sentences.** Non-streaming requests that explicitly set `temperature: 0` are cached in an
in-memory LRU with a TTL, keyed on a hash of the key ID, the alias and the canonical request body.
Clients can bypass it with `x-gateway-cache: bypass`, and every response says hit, miss or bypass.

## What I learned

- **Explicit zero only.** OpenAI's default temperature is 1, so a request that omits it is random
  and must not be cached.
- **`temperature: 0` is still not fully deterministic.** Providers can return different text for
  the same request (batching, hardware). The cache makes repeated answers identical, which is a
  behavior change worth knowing about.
- **Canonical JSON makes equal requests hash equal.** Sorted keys and fixed separators, with
  `stream`, `stream_options` and `user` removed because they do not change the answer.
- **Scope the cache per key.** Sharing across keys raises the hit rate but can hand one tenant a
  response generated for another. Correctness wins (D9).
- **A cache hit is free, but not invisible.** It still writes a usage row (`cache_hit = 1`,
  cost 0) and still counts against the rate limit.

## Metric

`gateway_cache_requests_total{result}`.
