# Milestone 3 implementation plan

Scope: OpenSpec tasks 3.1–3.8. Capability specs remain unchanged unless a
demonstrable contract flaw is found. Live provider dispatch remains disabled;
all reliability testing uses the deterministic mock and offline fixtures.

1. Introduce one injectable monotonic deadline with a 100 ms critical-recording
   margin and 50 ms local scheduler tolerance. Bound each admission, provider
   attempt, wait, backoff, and terminal write by remaining time.
2. Refactor execution into durable per-attempt records. Retry only classified
   transient failures, use bounded full jitter/Retry-After, cap retries at two
   per candidate and four total calls, and preserve policy candidate order and
   constraints on fallback. Give schema-validation recovery a separate class.
3. Add Redis-backed shared circuit and tenant/provider rate/concurrency leases
   with fail-closed admission, fenced half-open probes, expiry, and readiness
   degradation. Test two simulated replicas and dependency faults.
4. Add tenant/endpoint/key-scoped durable idempotency ownership with a complete
   canonical fingerprint and encrypted protected terminal replay. Include
   concurrent ownership, conflict, retention, cross-tenant isolation, and
   original-versus-ingress request IDs.
5. Add disconnect/crash/lease recovery that never assumes a timed-out upstream
   call was free or safe to redispatch. Preserve uncertain attempt evidence and
   test the defined failure precedence.
6. Run the integrated fault/security/regression matrix, rebuild the local
   container, browser-test the current API, update docs/task evidence, then
   commit and push only when all 3.x requirements are verified.

Atomic spend reservations belong to milestone 4; until then live dispatch
stays disabled and mock pricing is zero by default. No paid-provider or cloud
action is authorized by this plan.
