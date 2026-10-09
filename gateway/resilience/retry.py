"""The attempt loop: timeouts, retries, fallback and breaker bookkeeping, before the first byte.

Targets rotate: attempt n uses available[n % len(available)], where `available` is recomputed
each attempt from the capable targets whose breaker allows a call. With one target this is a
plain retry; with two it goes primary, fallback, primary.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator

from gateway import metrics, tracing
from gateway.config import Retries, Timeouts
from gateway.context import RequestContext, Target
from gateway.errors import BadRequest, GatewayError, Unavailable, UpstreamFailed, UpstreamTimeout
from gateway.providers.base import Provider, ProviderError
from gateway.resilience.breaker import Breaker
from gateway.schemas import ChatChunk, ChatResponse
from gateway.streams import shielded

log = logging.getLogger("gateway.retry")


def retryable(e: ProviderError) -> bool:
    if e.kind == "http":
        return e.status == 429 or (e.status or 0) >= 500
    return e.kind in ("connect", "network", "timeout_first_byte")


def record(breaker: Breaker, e: ProviderError) -> None:
    """Map an upstream failure to what the breaker should learn from it."""
    if e.kind == "http" and e.status == 429:
        breaker.release()  # the provider is up, just busy
    elif e.kind == "http" and (e.status or 0) < 500:
        breaker.record_success()  # the provider answered; the request was the problem
    else:
        breaker.record_failure()


def to_gateway_error(e: ProviderError) -> GatewayError:
    if e.kind.startswith("timeout"):
        return UpstreamTimeout("the upstream provider timed out")
    if e.kind == "http" and e.status in (400, 404, 422):
        return BadRequest(f"the upstream provider rejected the request: {e.message}")
    if e.kind == "http" and e.status in (401, 403):
        log.error("upstream authentication failed (status %s); check provider keys", e.status)
        return UpstreamFailed("the upstream provider refused the gateway's credentials")
    return UpstreamFailed(f"the upstream provider failed: {e}")


def remaining(deadline: float, cap: float) -> float:
    return max(0.0, min(cap, deadline - time.monotonic()))


class Attempts:
    def __init__(
        self,
        providers: dict[str, Provider],
        breakers: dict[str, Breaker],
        timeouts: Timeouts,
        retries: Retries,
    ) -> None:
        self.providers, self.breakers, self.t, self.r = providers, breakers, timeouts, retries

    def _pick(self, ctx: RequestContext, targets: list[Target], n: int) -> Target:
        available = [t for t in targets if self.breakers[t.provider].can_try()]
        if not available:
            raise Unavailable(
                "all providers for this model are failing; try again shortly", retry_after=1
            )
        target = available[n % len(available)]
        ctx.target, ctx.attempts = target, n + 1
        self.breakers[target.provider].begin()
        return target

    async def _after_failure(
        self, target: Target, err: ProviderError, n: int, deadline: float
    ) -> None:
        """Record the failure, then either raise or sleep before the next attempt."""
        record(self.breakers[target.provider], err)
        ok = retryable(err)
        metrics.upstream_attempts.labels(target.provider, "retryable" if ok else "fatal").inc()
        log.info("attempt %d on %s failed: %s", n + 1, target.provider, err)
        if not ok or n + 1 >= self.r.max_attempts:
            raise to_gateway_error(err)
        if err.retry_after is not None:
            delay = err.retry_after
        else:
            delay = random.uniform(0, self.r.backoff_base_s * 2**n)
        if time.monotonic() + delay >= deadline:
            raise to_gateway_error(err)
        await asyncio.sleep(delay)

    async def open_stream(
        self, ctx: RequestContext, targets: list[Target]
    ) -> tuple[ChatChunk, AsyncIterator[ChatChunk]]:
        """Return the first chunk and the rest of the stream, retrying before the first byte."""
        deadline = ctx.started_at + self.t.total_s
        for n in range(self.r.max_attempts):
            target = self._pick(ctx, targets, n)
            span = tracing.start_attempt_span(ctx.span, target.provider, target.model, n + 1)
            upstream = self.providers[target.provider].stream(ctx.body, target.model)
            try:
                async with asyncio.timeout(remaining(deadline, self.t.first_byte_s)):
                    first = await anext(upstream)
            except TimeoutError:
                err = ProviderError("timeout_first_byte")
            except StopAsyncIteration:
                err = ProviderError("protocol", message="empty stream")
            except ProviderError as e:
                err = e
            except BaseException:
                self.breakers[target.provider].release()
                await shielded(upstream.aclose())
                tracing.end(span, outcome="aborted")
                raise
            else:
                ctx.first_byte_at = time.monotonic()
                metrics.upstream_attempts.labels(target.provider, "ok").inc()
                tracing.end(span, outcome="ok")
                return first, upstream
            await shielded(upstream.aclose())
            tracing.end(span, outcome="failed", error=str(err))
            await self._after_failure(target, err, n, deadline)
        raise AssertionError("unreachable: the last attempt raises")

    async def complete(self, ctx: RequestContext, targets: list[Target]) -> ChatResponse:
        deadline = ctx.started_at + self.t.total_s
        for n in range(self.r.max_attempts):
            target = self._pick(ctx, targets, n)
            breaker = self.breakers[target.provider]
            span = tracing.start_attempt_span(ctx.span, target.provider, target.model, n + 1)
            try:
                async with asyncio.timeout(remaining(deadline, self.t.first_byte_s)):
                    resp = await self.providers[target.provider].complete(ctx.body, target.model)
            except TimeoutError:
                err = ProviderError("timeout_first_byte")
            except ProviderError as e:
                err = e
            except BaseException:
                breaker.release()
                tracing.end(span, outcome="aborted")
                raise
            else:
                ctx.first_byte_at = time.monotonic()
                breaker.record_success()
                metrics.upstream_attempts.labels(target.provider, "ok").inc()
                tracing.end(span, outcome="ok")
                return resp
            tracing.end(span, outcome="failed", error=str(err))
            await self._after_failure(target, err, n, deadline)
        raise AssertionError("unreachable: the last attempt raises")
