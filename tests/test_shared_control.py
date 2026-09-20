import os
from collections.abc import AsyncIterator, Awaitable
from typing import cast
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.domain.control import ControlLimits, ControlRejected, RedisControl
from app.domain.deadline import Deadline, SystemClock
from app.domain.models import StateUnavailable


@pytest.fixture
async def shared() -> AsyncIterator[tuple[Redis, str]]:
    url = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:56379")
    redis = Redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2)
    namespace = "test-" + uuid4().hex
    try:
        await redis.ping()
    except Exception:
        await redis.aclose()
        pytest.fail("Redis integration dependency unavailable; start the local Redis service.")
    try:
        yield redis, namespace
    finally:
        keys = [key async for key in redis.scan_iter(f"{namespace}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


def deadline(milliseconds: int = 1000) -> Deadline:
    clock = SystemClock()
    return Deadline(clock, clock.now(), milliseconds)


async def test_two_replicas_share_concurrency_rate_and_expired_leases(
    shared: tuple[Redis, str],
) -> None:
    redis, namespace = shared
    limits = ControlLimits(tenant_concurrency=1, provider_concurrency=1, tenant_rate_per_minute=2)
    first = RedisControl(redis, limits, namespace=namespace)
    second = RedisControl(redis, limits, namespace=namespace)
    tenant = uuid4()
    lease = await first.acquire(tenant, "mock", deadline())
    with pytest.raises(TimeoutError):
        await second.acquire(tenant, "mock", deadline(150))
    # Server-owned expiry, not local process bookkeeping, recovers lost leases.
    await redis.zadd(f"{namespace}:tenant:{tenant}:leases", {lease.lease_id: 0})
    await redis.zadd(f"{namespace}:provider:mock:leases", {lease.lease_id: 0})
    recovered = await second.acquire(tenant, "mock", deadline())
    await second.release(recovered, transient=False)
    with pytest.raises(ControlRejected, match="rate_limit"):
        await first.acquire(tenant, "mock", deadline())


async def test_open_half_open_probe_fence_across_replicas(shared: tuple[Redis, str]) -> None:
    redis, namespace = shared
    limits = ControlLimits(failure_threshold=2)
    first = RedisControl(redis, limits, namespace=namespace)
    second = RedisControl(redis, limits, namespace=namespace)
    tenant = uuid4()
    for _ in range(2):
        lease = await first.acquire(tenant, "mock", deadline())
        await first.release(lease, transient=True)
    snapshot = await second.snapshot("mock")
    assert snapshot.state == "open" and snapshot.transient_failures == 2
    with pytest.raises(ControlRejected, match="circuit_open"):
        await second.acquire(tenant, "mock", deadline())
    circuit = f"{namespace}:circuit:mock"
    await cast(Awaitable[object], redis.hset(circuit, "until", "0"))
    probe = await first.acquire(tenant, "mock", deadline())
    assert probe.probe_token > 0
    with pytest.raises(ControlRejected, match="circuit_probe_busy"):
        await second.acquire(tenant, "mock", deadline())
    # Expired ownership may be fenced; the old report must not close the circuit.
    await cast(Awaitable[object], redis.hset(circuit, "probe_until", "0"))
    newer = await second.acquire(tenant, "mock", deadline())
    assert newer.probe_token > probe.probe_token
    await first.release(probe, transient=False)
    assert await cast(Awaitable[object], redis.hget(circuit, "state")) == b"half_open"
    await second.release(newer, transient=False)
    assert await cast(Awaitable[object], redis.hget(circuit, "state")) is None


async def test_control_outage_fails_closed() -> None:
    redis = Redis.from_url("redis://127.0.0.1:1", socket_connect_timeout=0.05)
    control = RedisControl(redis, namespace="test-" + uuid4().hex)
    try:
        with pytest.raises(StateUnavailable):
            await control.acquire(uuid4(), "mock", deadline())
    finally:
        await redis.aclose()
