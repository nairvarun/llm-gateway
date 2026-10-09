"""One usage row per authenticated request, written when the response finishes.

The write is shielded so a client disconnect cannot drop it, and it never raises.
"""

from __future__ import annotations

import logging
import time

from gateway import metrics
from gateway.config import Price
from gateway.context import GatewayResponse, RequestContext, Usage
from gateway.errors import GatewayError
from gateway.pricing import cost_usd, estimate_tokens
from gateway.stages.base import Next
from gateway.store.base import Store, UsageRow, utc_ts
from gateway.streams import Outcome, shielded, wrap_stream

log = logging.getLogger("gateway.usage")


class UsageStage:
    def __init__(self, store: Store, pricing: dict[str, Price]) -> None:
        self.store, self.pricing = store, pricing

    async def __call__(self, ctx: RequestContext, call_next: Next) -> GatewayResponse:
        try:
            resp = await call_next(ctx)
        except GatewayError as e:
            ctx.status = e.status
            await shielded(self.record(ctx))
            raise
        if resp.stream is None:
            ctx.status = resp.status
            await shielded(self.record(ctx))
            return resp
        resp.stream = wrap_stream(
            resp.stream, None, lambda outcome: self.record_stream(ctx, outcome)
        )
        return resp

    async def record_stream(self, ctx: RequestContext, outcome: Outcome) -> None:
        if outcome == "cancelled":
            ctx.status = 499  # nginx convention: client closed request
        await self.record(ctx)

    async def record(self, ctx: RequestContext) -> None:
        try:
            if ctx.usage is None and ctx.target is not None and ctx.first_byte_at is not None:
                ctx.usage = Usage(
                    input_tokens=estimate_tokens(ctx.body.messages),
                    output_tokens=ctx.output_chars // 4,
                    estimated=True,
                )
                metrics.usage_estimated.inc()
            cost = 0.0
            if ctx.usage is not None and ctx.target is not None and not ctx.cache_hit:
                cost = cost_usd(self.pricing[ctx.target.model], ctx.usage)
                metrics.cost.labels(ctx.alias, ctx.target.provider).inc(cost)
            now = time.monotonic()
            row = UsageRow(
                request_id=ctx.request_id,
                key_id=ctx.key.id,
                ts=utc_ts(),
                model_alias=ctx.alias,
                provider=ctx.target.provider if ctx.target else None,
                model=ctx.target.model if ctx.target else None,
                attempts=ctx.attempts,
                input_tokens=ctx.usage.input_tokens if ctx.usage else None,
                output_tokens=ctx.usage.output_tokens if ctx.usage else None,
                usage_estimated=bool(ctx.usage and ctx.usage.estimated),
                cost_usd=cost,
                latency_ms=round((now - ctx.started_at) * 1000),
                ttft_ms=round((ctx.first_byte_at - ctx.started_at) * 1000)
                if ctx.first_byte_at is not None
                else None,
                status=ctx.status,
                cache_hit=ctx.cache_hit,
            )
            await self.store.record_usage(row)
        except Exception:
            metrics.usage_write_failures.inc()
            log.exception("usage write failed for %s", ctx.request_id)
