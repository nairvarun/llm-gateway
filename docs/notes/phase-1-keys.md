# Phase 1: virtual keys and auth

**In two sentences.** Clients authenticate with a random `gw-` key whose SHA-256 is the only
thing stored, and each key can be limited to a list of model aliases. The admin API uses a
separate token and refuses any virtual key with 403.

## What I learned

- **A fast hash is fine for high-entropy secrets.** bcrypt and argon2 exist to slow down guessing
  low-entropy passwords. A key with 256 random bits cannot be guessed, so SHA-256 gives the same
  protection and keeps the per-request lookup cheap.
- **Authentication and authorization fail differently.** An unknown or revoked key is 401 with
  the same message for both, so callers cannot probe which keys exist. A valid key asking for a
  model it may not use is 403.
- **Compare secrets in constant time.** `hmac.compare_digest` avoids leaking how many leading
  characters of the admin token matched.
- **Defense in layers.** `/admin` has its own token, refuses `gw-` keys, and is not routed by the
  Ingress at all. Any one layer failing still leaves the others.

## Metric

`gateway_auth_failures_total{reason}`: missing, invalid, forbidden_model.
