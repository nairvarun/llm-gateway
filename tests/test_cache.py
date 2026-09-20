import asyncio
import base64
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis

from app.cache.entry import open_entry, seal_entry
from app.cache.redis_cache import CacheUnavailable, RedisCache
from app.config import Settings
from app.domain.control import LocalControl, UnavailableControl
from app.main import create_app
from app.persistence.bootstrap import DEMO_SCHEMA, bootstrap_local
from app.persistence.store import PostgresStore
from app.providers.mock import MockProvider, MockStep
from app.security.replay import ReplayCipher

pytestmark = pytest.mark.integration
SECRET = base64.b64encode(b"synthetic-test-cache-key-32bytes").decode()
OPT_IN = {"cache_mode": "read_write", "cache_classification": "approved_non_sensitive"}


@pytest.fixture
async def cache_transport() -> AsyncIterator[tuple[Redis, RedisCache, str]]:
    url = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:56379")
    redis = Redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2)
    prefix = "test-cache:" + uuid4().hex
    try:
        await redis.ping()
    except Exception:
        await redis.aclose()
        pytest.fail("Redis integration dependency unavailable; start the local Redis service.")
    try:
        yield redis, RedisCache(redis, prefix=prefix), prefix
    finally:
        keys = [key async for key in redis.scan_iter(f"{prefix}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def test_cache_hit_zero_fresh_usage_and_scoped_evidence(
    postgres: PostgresStore, cache_transport: tuple[Redis, RedisCache, str]
) -> None:
    redis, cache, prefix = cache_transport
    key, tenant = await bootstrap_local(postgres)
    provider = MockProvider()
    app = create_app(
        Settings(cache_encryption_key=SECRET),
        store=postgres,
        provider=provider,
        control=LocalControl(),
        cache=cache,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-API-Key": key}
    ) as client:
        first = await client.post("/v1/generate", json={"input": "synthetic", **OPT_IN})
        second = await client.post("/v1/generate", json={"input": "synthetic", **OPT_IN})
    assert first.status_code == second.status_code == 200
    assert first.json()["cache_status"] == "miss"
    assert second.json()["cache_status"] == "hit"
    assert second.json()["cache_hit"] is True
    assert second.json()["usage"]["input_tokens"] == 0
    assert second.json()["usage"]["source_request_id"] == first.json()["request_id"]
    assert second.json()["estimated_cost_usd"] == "0"
    assert provider.invocations == 1
    evidence = await postgres.evidence(tenant, UUID(second.json()["request_id"]))
    assert evidence is not None and evidence.status == "completed"
    assert evidence.routing_evidence is not None and evidence.routing_evidence["cache_hit"] is True
    identity = first.json()["routing"]["cache_key_hash"]
    protected = await redis.get(f"{prefix}:1:1:{identity}:entry")
    assert protected is not None
    assert b"synthetic" not in protected
    assert key.encode() not in protected


async def test_read_only_misses_do_not_write_and_exact_invalidation_hides_old_entry(
    postgres: PostgresStore, cache_transport: tuple[Redis, RedisCache, str]
) -> None:
    _, cache, _ = cache_transport
    key, tenant = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    provider = MockProvider()
    app = create_app(
        Settings(cache_encryption_key=SECRET),
        store=postgres,
        provider=provider,
        control=LocalControl(),
        cache=cache,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-API-Key": key}
    ) as client:
        for _ in range(2):
            response = await client.post(
                "/v1/generate", json={"input": "x", **OPT_IN, "cache_mode": "read_only"}
            )
            assert response.status_code == 200 and response.json()["cache_status"] == "miss"
        populated = await client.post("/v1/generate", json={"input": "x", **OPT_IN})
        assert populated.status_code == 200
        hit = await client.post(
            "/v1/generate", json={"input": "x", **OPT_IN, "cache_mode": "read_only"}
        )
        assert hit.json()["cache_status"] == "hit"
        identity = populated.json()["routing"]["cache_key_hash"]
        await postgres.invalidate_cache_exact(
            operator, tenant.tenant_id, tenant.application_id, identity
        )
        after = await client.post("/v1/generate", json={"input": "x", **OPT_IN})
    assert after.status_code == 200 and after.json()["cache_status"] == "miss"
    assert provider.invocations == 4


async def test_namespace_invalidation_and_provider_disable_recheck(
    postgres: PostgresStore, cache_transport: tuple[Redis, RedisCache, str]
) -> None:
    _, cache, _ = cache_transport
    key, tenant = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    provider = MockProvider()
    app = create_app(
        Settings(cache_encryption_key=SECRET),
        store=postgres,
        provider=provider,
        control=LocalControl(),
        cache=cache,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-API-Key": key}
    ) as client:
        first = await client.post("/v1/generate", json={"input": "x", **OPT_IN})
        await postgres.invalidate_cache_namespace(operator, tenant.tenant_id, tenant.application_id)
        second = await client.post("/v1/generate", json={"input": "x", **OPT_IN})
        await postgres.set_provider_disabled(operator, "mock", True)
        blocked = await client.post("/v1/generate", json={"input": "x", **OPT_IN})
    assert first.status_code == second.status_code == 200
    assert second.json()["cache_status"] == "miss"
    assert blocked.status_code == 503
    assert provider.invocations == 2


async def test_expired_or_invalid_extraction_entry_misses(
    postgres: PostgresStore, cache_transport: tuple[Redis, RedisCache, str]
) -> None:
    redis, cache, prefix = cache_transport
    key, _ = await bootstrap_local(postgres)
    provider = MockProvider()
    app = create_app(
        Settings(cache_encryption_key=SECRET, cache_ttl_seconds=1),
        store=postgres,
        provider=provider,
        control=LocalControl(),
        cache=cache,
    )
    payload = {"input": '{"count":2}', "json_schema": DEMO_SCHEMA, **OPT_IN}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-API-Key": key}
    ) as client:
        first = await client.post("/v1/extract", json=payload)
        assert first.status_code == 200
        identity = first.json()["routing"]["cache_key_hash"]
        entry_key = f"{prefix}:1:1:{identity}:entry"
        raw = await redis.get(entry_key)
        assert raw is not None
        cipher = ReplayCipher(SECRET)
        parsed = open_entry(
            cipher, UUID(first.json()["routing"]["policy_id"]), "/v1/extract", "x", raw
        )
        assert parsed is None  # A different tenant/AAD cannot decrypt the entry.
        # Re-seal a schema-invalid value under the same authenticated identity.
        from app.security.auth import hash_api_key

        principal = await postgres.authenticate(hash_api_key(key))
        assert principal is not None
        valid = open_entry(cipher, principal.tenant_id, "/v1/extract", f"1:1:{identity}", raw)
        assert valid is not None
        invalid_response = {**valid.response, "output": {"wrong": True}}
        invalid = seal_entry(
            cipher,
            principal.tenant_id,
            "/v1/extract",
            f"1:1:{identity}",
            valid.source_request_id,
            invalid_response,
            1,
        )
        await redis.set(entry_key, invalid, ex=1)
        second = await client.post("/v1/extract", json=payload)
        assert second.status_code == 200 and second.json()["cache_status"] == "miss"
        await asyncio.sleep(1.05)
        third = await client.post("/v1/extract", json=payload)
    assert third.status_code == 200 and third.json()["cache_status"] == "miss"
    assert provider.invocations == 3


@pytest.mark.parametrize("_case", range(5))
async def test_concurrent_misses_share_one_owner(
    postgres: PostgresStore, cache_transport: tuple[Redis, RedisCache, str], _case: int
) -> None:
    _, cache, _ = cache_transport
    key, _ = await bootstrap_local(postgres)
    provider = MockProvider([MockStep(delay_seconds=0.15)])
    app = create_app(
        Settings(cache_encryption_key=SECRET),
        store=postgres,
        provider=provider,
        control=LocalControl(),
        cache=cache,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-API-Key": key}
    ) as client:
        first, second = await asyncio.gather(
            client.post("/v1/generate", json={"input": "same", **OPT_IN}),
            client.post("/v1/generate", json={"input": "same", **OPT_IN}),
        )
    assert first.status_code == second.status_code == 200
    assert sorted([first.json()["cache_status"], second.json()["cache_status"]]) == ["hit", "miss"]
    assert provider.invocations == 1


async def test_namespace_invalidation_fences_slow_writer(
    postgres: PostgresStore, cache_transport: tuple[Redis, RedisCache, str]
) -> None:
    _, cache, _ = cache_transport
    key, tenant = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    provider = MockProvider([MockStep(delay_seconds=0.2)])
    app = create_app(
        Settings(cache_encryption_key=SECRET),
        store=postgres,
        provider=provider,
        control=LocalControl(),
        cache=cache,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-API-Key": key}
    ) as client:
        first_task = asyncio.create_task(
            client.post("/v1/generate", json={"input": "slow", **OPT_IN})
        )
        for _ in range(50):
            if provider.invocations:
                break
            await asyncio.sleep(0.01)
        assert provider.invocations == 1
        await postgres.invalidate_cache_namespace(operator, tenant.tenant_id, tenant.application_id)
        first = await first_task
        second = await client.post("/v1/generate", json={"input": "slow", **OPT_IN})
    assert first.status_code == second.status_code == 200
    assert second.json()["cache_status"] == "miss"
    assert provider.invocations == 2


async def test_corruption_expiry_and_fencing_are_unusable(
    postgres: PostgresStore, cache_transport: tuple[Redis, RedisCache, str]
) -> None:
    redis, cache, prefix = cache_transport
    cipher = ReplayCipher(SECRET)
    tenant = uuid4()
    source = uuid4()
    protected = seal_entry(
        cipher, tenant, "/v1/generate", "1:1:deadbeef", source, {"output": "x"}, 1
    )
    assert (
        open_entry(
            cipher,
            tenant,
            "/v1/generate",
            "1:1:deadbeef",
            protected,
            now=datetime.now(UTC) + timedelta(seconds=2),
        )
        is None
    )
    assert open_entry(cipher, tenant, "/v1/generate", "1:2:deadbeef", protected) is None
    assert open_entry(cipher, tenant, "/v1/generate", "1:1:deadbeef", b"garbage") is None

    identity = "1:1:" + "a" * 64
    old = await cache.claim(identity, 1000)
    assert old is not None
    await redis.delete(f"{prefix}:{identity}:lease")
    newer = await cache.claim(identity, 1000)
    assert newer is not None and newer != old
    assert not await cache.publish(identity, old, b"stale", 60)
    assert await cache.publish(identity, newer, b"fresh", 60)
    assert await cache.read(identity) == b"fresh"


async def test_cache_only_outage_degrades_while_critical_controls_remain_healthy(
    postgres: PostgresStore,
) -> None:
    class BrokenCache:
        async def read(self, key: str) -> bytes | None:
            raise CacheUnavailable()

        async def claim(self, key: str, lease_ms: int) -> str | None:
            raise CacheUnavailable()

        async def publish(self, key: str, token: str, content: bytes, ttl_seconds: int) -> bool:
            raise CacheUnavailable()

        async def release(self, key: str, token: str) -> None:
            raise CacheUnavailable()

    key, _ = await bootstrap_local(postgres)
    provider = MockProvider()
    app = create_app(
        Settings(cache_encryption_key=SECRET),
        store=postgres,
        provider=provider,
        control=LocalControl(),
        cache=BrokenCache(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-API-Key": key}
    ) as client:
        response = await client.post("/v1/generate", json={"input": "synthetic", **OPT_IN})
    assert response.status_code == 200 and response.json()["cache_status"] == "degraded"
    assert provider.invocations == 1


async def test_busy_single_flight_waiter_stops_at_own_deadline(postgres: PostgresStore) -> None:
    class BusyCache:
        async def read(self, key: str) -> bytes | None:
            return None

        async def claim(self, key: str, lease_ms: int) -> str | None:
            return None

        async def publish(self, key: str, token: str, content: bytes, ttl_seconds: int) -> bool:
            return False

        async def release(self, key: str, token: str) -> None:
            return None

    key, _ = await bootstrap_local(postgres)
    provider = MockProvider()
    app = create_app(
        Settings(cache_encryption_key=SECRET),
        store=postgres,
        provider=provider,
        control=LocalControl(),
        cache=BusyCache(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-API-Key": key}
    ) as client:
        response = await client.post(
            "/v1/generate", json={"input": "busy", "latency_budget_ms": 250, **OPT_IN}
        )
    assert response.status_code == 504
    assert provider.invocations == 0


async def test_cache_degradation_does_not_bypass_unavailable_critical_control(
    postgres: PostgresStore,
) -> None:
    key, _ = await bootstrap_local(postgres)
    provider = MockProvider()
    app = create_app(
        Settings(cache_encryption_key=SECRET),
        store=postgres,
        provider=provider,
        control=UnavailableControl(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test", headers={"X-API-Key": key}
    ) as client:
        response = await client.post("/v1/generate", json={"input": "synthetic", **OPT_IN})
    assert response.status_code == 503
    assert provider.invocations == 0
