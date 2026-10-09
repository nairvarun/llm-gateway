"""Who is calling: hash the bearer key, look it up, check the alias list."""

from __future__ import annotations

from gateway import metrics
from gateway.context import GatewayResponse, RequestContext
from gateway.errors import Forbidden, Unauthorized
from gateway.stages.base import Next
from gateway.store.base import Store, VirtualKey
from gateway.store.sqlite import hash_key


async def authenticate(store: Store, bearer: str | None) -> VirtualKey:
    if not bearer:
        metrics.auth_failures.labels("missing").inc()
        raise Unauthorized("missing API key; send it as 'Authorization: Bearer <key>'")
    key = await store.get_key_by_hash(hash_key(bearer))
    if key is None or key.revoked_at is not None:
        # Same message for unknown and revoked, so callers cannot probe which keys exist.
        metrics.auth_failures.labels("invalid").inc()
        raise Unauthorized("invalid API key")
    return key


def allowed(key: VirtualKey, alias: str) -> bool:
    return key.allowed_models is None or alias in key.allowed_models


class Auth:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def __call__(self, ctx: RequestContext, call_next: Next) -> GatewayResponse:
        key = await authenticate(self.store, ctx.bearer)
        ctx.bearer = None
        if not allowed(key, ctx.alias):
            metrics.auth_failures.labels("forbidden_model").inc()
            raise Forbidden(f"this key may not use model {ctx.alias!r}")
        ctx.key = key
        return await call_next(ctx)
