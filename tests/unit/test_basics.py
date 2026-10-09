"""SSE parsing, the stream wrapper, pricing, the token bucket and the breaker."""

from __future__ import annotations

import asyncio

import pytest

from gateway.config import Price
from gateway.context import Usage
from gateway.limiter.memory import MemoryLimiter
from gateway.pricing import cost_usd, estimate_tokens
from gateway.resilience.breaker import Breaker
from gateway.sse import parse_sse
from gateway.streams import wrap_stream
from tests.fakes.clock import FakeClock


async def lines(*items: str):
    for item in items:
        yield item


# --- SSE ---------------------------------------------------------------------------------


async def test_parse_sse_events_and_comments():
    events = [
        e
        async for e in parse_sse(
            lines(
                ": keep-alive", "event: ping", "data: {}", "", "data: a", "data: b", "", "data: z"
            )
        )
    ]
    assert [(e.event, e.data) for e in events] == [("ping", "{}"), (None, "a\nb"), (None, "z")]


# --- wrap_stream --------------------------------------------------------------------------


async def test_wrap_stream_completed_and_order():
    log: list[str] = []

    async def inner():
        try:
            yield 1
            yield 2
        finally:
            log.append("inner closed")

    async def on_close(outcome):
        log.append(f"outer closed: {outcome}")

    seen = []
    items = [i async for i in wrap_stream(inner(), seen.append, on_close)]
    assert items == [1, 2] and seen == [1, 2]
    assert log == ["inner closed", "outer closed: completed"]


async def test_wrap_stream_cancelled_closes_inner_first():
    log: list[str] = []

    async def inner():
        try:
            yield 1
            await asyncio.sleep(10)
            yield 2
        finally:
            log.append("inner closed")

    async def on_close(outcome):
        log.append(f"outer closed: {outcome}")

    stream = wrap_stream(inner(), None, on_close)
    assert await anext(stream) == 1
    await stream.aclose()  # the consumer goes away mid-stream
    assert log == ["inner closed", "outer closed: cancelled"]


async def test_wrap_stream_error_outcome():
    outcomes = []

    async def inner():
        yield 1
        raise RuntimeError("boom")

    async def on_close(outcome):
        outcomes.append(outcome)

    with pytest.raises(RuntimeError):
        [i async for i in wrap_stream(inner(), None, on_close)]
    assert outcomes == ["error"]


async def test_on_close_survives_task_cancellation():
    done = asyncio.Event()

    async def inner():
        yield 1
        await asyncio.sleep(10)

    async def on_close(outcome):
        await asyncio.sleep(0.05)  # a slow write that must still finish
        done.set()

    async def consume():
        async for _ in wrap_stream(inner(), None, on_close):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(done.wait(), 1)


# --- pricing ------------------------------------------------------------------------------


def test_cost_and_estimate():
    assert cost_usd(Price(input=3, output=15), Usage(1000, 100)) == 0.0045
    msgs = [{"role": "user", "content": "x" * 40}, {"role": "user", "content": [{"text": "yyyy"}]}]
    assert estimate_tokens(msgs) == 11 + 8


# --- token bucket -------------------------------------------------------------------------


async def test_token_bucket_refill():
    clock = FakeClock()
    lim = MemoryLimiter(now=clock.now)
    for _ in range(60):
        assert (await lim.acquire("k", 60))[0]
    allowed, retry_after = await lim.acquire("k", 60)
    assert not allowed and retry_after == pytest.approx(1.0)
    clock.advance(1)
    assert (await lim.acquire("k", 60))[0]
    assert (await lim.acquire("other", 60))[0]  # buckets are per key


# --- breaker ------------------------------------------------------------------------------


def test_breaker_opens_and_skips():
    clock = FakeClock()
    b = Breaker("p", threshold=5, open_s=30, now=clock.now)
    for _ in range(5):
        assert b.can_try()
        b.record_failure()
    assert b.state == "open" and not b.can_try()
    clock.advance(30)
    assert b.can_try()  # half-open
    b.begin()
    assert not b.can_try()  # only one trial at a time
    b.record_success()
    assert b.state == "closed" and b.can_try()


def test_half_open_failure_reopens():
    clock = FakeClock()
    b = Breaker("p", threshold=1, open_s=30, now=clock.now)
    b.record_failure()
    clock.advance(30)
    assert b.can_try()
    b.begin()
    b.record_failure()
    assert b.state == "open" and not b.can_try()


def test_half_open_trial_released_on_429():
    clock = FakeClock()
    b = Breaker("p", threshold=1, open_s=30, now=clock.now)
    b.record_failure()
    clock.advance(30)
    b.can_try()
    b.begin()
    b.release()  # the trial ended in a 429 or a client cancel
    assert b.state == "half_open" and b.can_try()


def test_success_resets_failure_count():
    b = Breaker("p", threshold=3, open_s=30)
    b.record_failure()
    b.record_failure()
    b.record_success()
    b.record_failure()
    assert b.state == "closed"
