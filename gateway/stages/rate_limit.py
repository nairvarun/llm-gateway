"""Requests per minute per key, with a token bucket. 429 + Retry-After when empty."""

from __future__ import annotations

import math

from gateway import metrics
from gateway.context import GatewayResponse, RequestContext
from gateway.errors import RateLimited
from gateway.limiter.base import Limiter
from gateway.stages.base import Next


class RateLimit:
    def __init__(self, limiter: Limiter) -> None:
        self.limiter = limiter

    async def __call__(self, ctx: RequestContext, call_next: Next) -> GatewayResponse:
        rpm = ctx.key.rpm_limit
        if rpm is not None:
            allowed, retry_after = await self.limiter.acquire(ctx.key.id, rpm)
            if not allowed:
                metrics.rate_limited.inc()
                raise RateLimited(
                    f"rate limit of {rpm} requests per minute exceeded",
                    retry_after=max(1, math.ceil(retry_after)),
                )
        return await call_next(ctx)
