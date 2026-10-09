"""The terminal stage: pick capable targets, call a provider, relay the result.

For streams it waits for the first chunk before returning. Until then nothing has been sent,
so a failure can still become a retry or a proper 502/504.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from gateway.config import Config
from gateway.context import GatewayResponse, RequestContext, Target, Usage
from gateway.errors import BadRequest, UpstreamFailed, UpstreamTimeout
from gateway.providers.base import Provider, ProviderError
from gateway.resilience.breaker import Breaker
from gateway.resilience.retry import Attempts, remaining
from gateway.schemas import ChatChunk, ChatRequest
from gateway.streams import StreamError, StreamItem, shielded


def content_chars(chunk: ChatChunk) -> int:
    total = 0
    for choice in chunk.choices:
        content = (choice.get("delta") or {}).get("content")
        if isinstance(content, str):
            total += len(content)
    return total


def usage_of(raw: dict) -> Usage:
    return Usage(int(raw.get("prompt_tokens", 0)), int(raw.get("completion_tokens", 0)))


class Route:
    def __init__(
        self, cfg: Config, providers: dict[str, Provider], breakers: dict[str, Breaker]
    ) -> None:
        self.cfg, self.providers, self.breakers = cfg, providers, breakers
        self.attempts = Attempts(providers, breakers, cfg.timeouts, cfg.retries)

    def capable(self, target: Target, body: ChatRequest) -> bool:
        return not body.tools or self.providers[target.provider].supports_tools

    async def __call__(self, ctx: RequestContext) -> GatewayResponse:
        targets = [
            Target(t.provider, t.model)
            for t in self.cfg.models[ctx.alias].targets
            if self.capable(Target(t.provider, t.model), ctx.body)
        ]
        if not targets:
            raise BadRequest(f"no target for model {ctx.alias!r} supports this request (tools)")
        if ctx.body.stream:
            first, upstream = await self.attempts.open_stream(ctx, targets)
            return GatewayResponse(stream=self.relay(ctx, first, upstream))
        resp = await self.attempts.complete(ctx, targets)
        if resp.usage:
            ctx.usage = usage_of(resp.usage)
        body = resp.model_dump(exclude_none=True)
        body["model"] = ctx.alias
        return GatewayResponse(body=body)

    async def relay(
        self, ctx: RequestContext, first: ChatChunk, upstream: AsyncIterator[ChatChunk]
    ) -> AsyncIterator[StreamItem]:
        deadline = ctx.started_at + self.cfg.timeouts.total_s
        idle_s = self.cfg.timeouts.idle_s
        wants_usage = bool((ctx.body.stream_options or {}).get("include_usage"))
        breaker = self.breakers[ctx.target.provider]
        outcome: str | None = None  # None means the client went away
        item = first
        try:
            while True:
                if item.usage:
                    ctx.usage = usage_of(item.usage)
                ctx.output_chars += content_chars(item)
                if item.choices or wants_usage:
                    yield item.model_copy(update={"model": ctx.alias})
                try:
                    async with asyncio.timeout(remaining(deadline, idle_s)):
                        item = await anext(upstream)
                except StopAsyncIteration:
                    outcome = "ok"
                    break
        except (TimeoutError, ProviderError) as e:
            outcome = "failed"
            timed_out = isinstance(e, TimeoutError) or e.kind.startswith("timeout")
            err = (
                UpstreamTimeout("the upstream provider stopped sending")
                if timed_out
                else UpstreamFailed(f"the upstream stream failed: {e}")
            )
            ctx.status, ctx.error = err.status, err
            yield StreamError(err)
        finally:

            async def cleanup() -> None:
                await upstream.aclose()
                if outcome == "ok":
                    breaker.record_success()
                elif outcome == "failed":
                    breaker.record_failure()
                else:
                    breaker.release()  # client cancelled: says nothing about the provider

            await shielded(cleanup())
