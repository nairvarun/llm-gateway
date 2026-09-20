# Milestone 4 implementation plan

This phase remains offline and mock-backed. The OpenSpec capability specs and
tasks are unchanged; no live-provider spend or cloud provisioning is authorized.

1. Add PostgreSQL UTC daily/monthly budget buckets and one reservation per
   attempt. Lock bucket rows in a stable order and enforce the persisted request
   ceiling before an attempt can dispatch. Test competing transactions.
2. Reconcile known usage once, retain unknown holds, record overruns, and provide
   an audited conservative recovery path. Keep pre-dispatch and terminal writes
   on the correctness path and test crash/failure cases.
3. Add exact, tenant/application-scoped canonical identity and explicit policy
   eligibility. Protect content with an application encryption key and bounded
   TTL; recheck model constraints and extraction schemas on hits.
4. Coordinate same-key misses with fenced ownership and invalidation generations.
   Keep cache failures optional only while budget, idempotency, and shared
   admission controls remain healthy.
5. Add scoped spend queries, retention jobs, and an integrated fault/security
   matrix; then rerun quality gates, local containers, and browser checks before
   checking tasks, updating status docs, committing, and pushing.

The existing live-provider dispatch gate stays closed throughout this milestone
until every budget/reliability gate is complete; a paid smoke still requires
separate explicit authorization and a recorded ceiling.
