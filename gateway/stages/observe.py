"""Outermost stage: metrics, one JSON log line and a trace span for every request."""

from __future__ import annotations

import json
import logging
import time

from gateway import metrics, tracing
from gateway.context import GatewayResponse, RequestContext
from gateway.errors import GatewayError
from gateway.stages.base import Next
from gateway.streams import Outcome, wrap_stream

log = logging.getLogger("gateway.access")


class Observe:
    async def __call__(self, ctx: RequestContext, call_next: Next) -> GatewayResponse:
        metrics.in_flight.inc()
        ctx.span = tracing.start_request_span(ctx.request_id, ctx.alias)
        try:
            resp = await call_next(ctx)
        except GatewayError as e:
            ctx.status = e.status
            self.finish(ctx)
            raise
        except BaseException:
            ctx.status = 500
            self.finish(ctx)
            raise
        if resp.stream is None:
            ctx.status = resp.status
            self.finish(ctx)
            return resp
        resp.stream = wrap_stream(
            resp.stream, None, lambda outcome: self.finish_async(ctx, outcome)
        )
        return resp

    async def finish_async(self, ctx: RequestContext, outcome: Outcome) -> None:
        if outcome == "cancelled":
            ctx.status = 499
        self.finish(ctx)

    def finish(self, ctx: RequestContext) -> None:
        metrics.in_flight.dec()
        elapsed = time.monotonic() - ctx.started_at
        provider = ctx.target.provider if ctx.target else "none"
        metrics.requests.labels(ctx.alias, provider, metrics.status_class(ctx.status)).inc()
        metrics.duration.labels(ctx.alias, provider).observe(elapsed)
        ttft_ms = None
        if ctx.first_byte_at is not None:
            ttft = ctx.first_byte_at - ctx.started_at
            metrics.ttft.labels(ctx.alias, provider).observe(ttft)
            ttft_ms = round(ttft * 1000)
        if ctx.usage is not None:
            metrics.tokens.labels(ctx.alias, provider, "input").inc(ctx.usage.input_tokens)
            metrics.tokens.labels(ctx.alias, provider, "output").inc(ctx.usage.output_tokens)
        line = {
            "event": "request",
            "request_id": ctx.request_id,
            "key_id": ctx.key.id if ctx.key else None,
            "alias": ctx.alias,
            "provider": provider,
            "model": ctx.target.model if ctx.target else None,
            "status": ctx.status,
            "attempts": ctx.attempts,
            "latency_ms": round(elapsed * 1000),
            "ttft_ms": ttft_ms,
            "cache_hit": ctx.cache_hit,
            "input_tokens": ctx.usage.input_tokens if ctx.usage else None,
            "output_tokens": ctx.usage.output_tokens if ctx.usage else None,
            "usage_estimated": ctx.usage.estimated if ctx.usage else None,
            "error": ctx.error.message if ctx.error else None,
        }
        log.info(json.dumps(line))
        tracing.end(ctx.span, status=ctx.status, provider=provider, attempts=ctx.attempts)
