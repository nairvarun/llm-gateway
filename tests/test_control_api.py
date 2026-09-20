import os
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis

from app.config import Settings
from app.domain.control import ControlLimits, RedisControl
from app.main import create_app
from app.providers.mock import MockProvider, MockStep
from tests.fakes import MemoryStore


@pytest.mark.integration
async def test_api_rate_and_circuit_fail_closed_across_instances() -> None:
    url = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:56379")
    redis = Redis.from_url(url)
    namespace = "test-" + uuid4().hex
    try:
        await redis.ping()
        limits = ControlLimits(failure_threshold=2, tenant_rate_per_minute=3)
        store = MemoryStore()
        provider = MockProvider([MockStep("server")])
        first = create_app(
            Settings(),
            store=store,
            provider=provider,
            control=RedisControl(redis, limits, namespace=namespace),
        )
        second = create_app(
            Settings(),
            store=store,
            provider=provider,
            control=RedisControl(redis, limits, namespace=namespace),
        )
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(first),
                base_url="http://test",
                headers={"X-API-Key": store.key},
            ) as a,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(second),
                base_url="http://test",
                headers={"X-API-Key": store.key},
            ) as b,
        ):
            first_error = await a.post("/v1/generate", json={"input": "synthetic"})
            second_error = await b.post("/v1/generate", json={"input": "synthetic"})
            rejected = await a.post("/v1/generate", json={"input": "synthetic"})
        assert first_error.status_code == second_error.status_code == 503
        assert rejected.status_code == 503
        assert rejected.json()["error"]["code"] == "CIRCUIT_OPEN"
        assert provider.invocations == 2
        rejected_id = UUID(rejected.json()["request_id"])
        assert rejected_id not in store.attempts
        evidence = store.records[rejected_id].routing_evidence
        assert evidence is not None
        assert evidence["health_snapshot"] == {"mock": "0"}
    finally:
        keys = [key async for key in redis.scan_iter(f"{namespace}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
