import asyncio
import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.api.schemas import GenerateRequest
from app.config import Settings
from app.domain.models import IdempotencyClaim, ProviderInput, ProviderResult
from app.main import create_app
from app.persistence.bootstrap import bootstrap_local
from app.persistence.models import (
    Attempt,
    IdempotencyIngress,
    IdempotencyRecord,
    RequestRecord,
    UsageEvent,
)
from app.persistence.store import PostgresStore
from app.providers.mock import MockProvider, MockStep
from app.security.replay import ReplayCipher
from app.service import GatewayService
from tests.fakes import MemoryStore

KEY = base64.b64encode(bytes(range(32))).decode()


def keyed_settings() -> Settings:
    return Settings(replay_encryption_key=KEY)


async def test_completed_replay_conflict_and_encrypted_storage() -> None:
    store = MemoryStore()
    provider = MockProvider()
    app = create_app(keyed_settings(), store=store, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        headers={"X-API-Key": store.key},
    ) as client:
        original = await client.post(
            "/v1/generate", json={"input": "private synthetic phrase", "idempotency_key": "key-1"}
        )
        replay = await client.post(
            "/v1/generate", json={"input": "private synthetic phrase", "idempotency_key": "key-1"}
        )
        conflict = await client.post(
            "/v1/generate", json={"input": "different", "idempotency_key": "key-1"}
        )
    assert original.status_code == replay.status_code == 200
    assert provider.invocations == 1
    assert replay.json()["request_id"] != original.json()["request_id"]
    assert replay.json()["original_request_id"] == original.json()["request_id"]
    assert replay.json()["idempotency_replayed"] is True
    assert replay.json()["usage"]["source_request_id"] == original.json()["request_id"]
    assert replay.json()["usage"]["attempt_count"] == 0
    assert replay.json()["estimated_cost_usd"] == "0"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(store.keyed) == 1
    record = next(iter(store.keyed.values()))
    assert record[3] is not None
    assert b"private synthetic phrase" not in record[3]
    assert len(store.keyed_ingress) == 2


async def test_failed_execution_replay_and_tamper_detection() -> None:
    store = MemoryStore()
    provider = MockProvider([MockStep("invalid_request")])
    app = create_app(keyed_settings(), store=store, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        headers={"X-API-Key": store.key},
    ) as client:
        data = {"input": "synthetic", "idempotency_key": "fail-key"}
        original = await client.post("/v1/generate", json=data)
        replay = await client.post("/v1/generate", json=data)
        assert (original.status_code, replay.status_code) == (502, 502)
        assert replay.json()["error"]["code"] == "UPSTREAM_REQUEST_REJECTED"
        assert replay.json()["original_request_id"] == original.json()["request_id"]
        assert provider.invocations == 1
        identity, row = next(iter(store.keyed.items()))
        assert row[3] is not None
        store.keyed[identity] = (*row[:3], row[3][:-1] + b"x", *row[4:])
        tampered = await client.post("/v1/generate", json=data)
        assert tampered.status_code == 503
        assert provider.invocations == 1


class BlockingProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()

    async def invoke(self, request: ProviderInput) -> ProviderResult:
        self.entered.set()
        await self.resume.wait()
        return await super().invoke(request)


async def test_concurrent_owner_and_stale_owner_never_redispatch() -> None:
    store = MemoryStore()
    provider = BlockingProvider()
    app = create_app(keyed_settings(), store=store, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        headers={"X-API-Key": store.key},
    ) as client:
        data = {"input": "synthetic", "idempotency_key": "same-key"}
        owner = asyncio.create_task(client.post("/v1/generate", json=data))
        await asyncio.wait_for(provider.entered.wait(), 1)
        duplicate = await client.post("/v1/generate", json=data)
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "REQUEST_IN_PROGRESS"
        assert duplicate.headers["Retry-After"] == "1"
        identity, row = next(iter(store.keyed.items()))
        store.keyed[identity] = (*row[:5], datetime.now(UTC) - timedelta(seconds=1))
        stale = await client.post("/v1/generate", json=data)
        assert stale.json()["error"]["code"] == "EXECUTION_UNCERTAIN"
        assert store.keyed[identity][2] == "uncertain"
        provider.resume.set()
        # A worker still alive after its ownership lease expired cannot seal
        # a now-uncertain key as a safe completed replay.
        finish = await owner
        assert finish.status_code == 503
        assert provider.invocations == 1


async def test_expiry_and_authenticated_tenant_isolation() -> None:
    store = MemoryStore()
    provider = MockProvider()
    service = GatewayService(keyed_settings(), store, provider)
    data = GenerateRequest(input="synthetic", idempotency_key="shared-literal")
    first = await service.execute(data, store.principal, uuid4(), service.clock.now())
    second_principal = replace(store.principal, tenant_id=uuid4())
    independent = await service.execute(data, second_principal, uuid4(), service.clock.now())
    assert first.idempotency_replayed is False
    assert independent.idempotency_replayed is False
    assert provider.invocations == 2
    identity = next(
        identity for identity in store.keyed if identity[0] == store.principal.tenant_id
    )
    row = store.keyed[identity]
    store.keyed[identity] = (*row[:4], datetime.now(UTC) - timedelta(seconds=1), row[5])
    renewed = await service.execute(data, store.principal, uuid4(), service.clock.now())
    assert renewed.idempotency_replayed is False
    assert provider.invocations == 3


@pytest.mark.integration
async def test_postgres_claim_replay_ingress_and_encryption(postgres: PostgresStore) -> None:
    key, principal = await bootstrap_local(postgres)
    provider = MockProvider()
    app = create_app(keyed_settings(), store=postgres, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        headers={"X-API-Key": key},
    ) as client:
        original = await client.post(
            "/v1/generate", json={"input": "sensitive synthetic text", "idempotency_key": "k"}
        )
        replay = await client.post(
            "/v1/generate", json={"input": "sensitive synthetic text", "idempotency_key": "k"}
        )
    assert (original.status_code, replay.status_code) == (200, 200)
    assert provider.invocations == 1
    async with postgres.sessions() as session:
        record = await session.scalar(select(IdempotencyRecord))
        ingress = await session.scalar(select(IdempotencyIngress))
        assert record is not None and ingress is not None
        assert record.tenant_id == principal.tenant_id
        assert record.status == "completed"
        assert record.encrypted_result is not None
        assert b"sensitive synthetic text" not in record.encrypted_result
        assert ingress.original_request_id == UUID(original.json()["request_id"])
        assert ingress.ingress_request_id == UUID(replay.json()["request_id"])


@pytest.mark.integration
async def test_postgres_expired_owner_recovers_uncertain_without_dispatch(
    postgres: PostgresStore,
) -> None:
    _, principal = await bootstrap_local(postgres)
    cipher = ReplayCipher(KEY)
    endpoint, literal = "/v1/generate", "stale-key"
    key_hash = cipher.key_hash(principal.tenant_id, endpoint, literal)
    fingerprint = cipher.fingerprint(principal, endpoint, GenerateRequest(input="x"), None)
    now = datetime.now(UTC)
    original_id = uuid4()
    owner = await postgres.claim_idempotency(
        principal,
        endpoint,
        key_hash,
        fingerprint,
        original_id,
        now + timedelta(hours=1),
        now - timedelta(seconds=1),
    )
    assert owner.status == "owned"
    await postgres.begin(
        principal, original_id, endpoint, "0" * 64, None, await postgres.snapshot()
    )
    duplicate = await postgres.claim_idempotency(
        principal,
        endpoint,
        key_hash,
        fingerprint,
        uuid4(),
        now + timedelta(hours=1),
        now + timedelta(seconds=10),
    )
    assert duplicate.status == "uncertain"
    assert duplicate.original_request_id == original_id
    again = await postgres.claim_idempotency(
        principal,
        endpoint,
        key_hash,
        fingerprint,
        uuid4(),
        now + timedelta(hours=1),
        now + timedelta(seconds=10),
    )
    assert again.status == "uncertain"
    async with postgres.sessions() as session:
        request = await session.get(RequestRecord, original_id)
        attempt = await session.scalar(select(Attempt).where(Attempt.request_id == original_id))
        assert request is not None and request.status == "uncertain"
        assert attempt is not None and attempt.outcome == "uncertain"
        assert attempt.error_class == "worker_lost"
        usage = await session.scalar(select(UsageEvent).where(UsageEvent.attempt_id == attempt.id))
        assert usage is not None and usage.usage_status == "unknown"
        assert usage.estimated_cost_usd == attempt.reserved_upper_cost_usd


@pytest.mark.integration
async def test_postgres_identical_concurrent_claims_have_one_owner(postgres: PostgresStore) -> None:
    _, principal = await bootstrap_local(postgres)
    now = datetime.now(UTC)

    async def claim() -> IdempotencyClaim:
        return await postgres.claim_idempotency(
            principal,
            "/v1/generate",
            "1" * 64,
            "2" * 64,
            uuid4(),
            now + timedelta(hours=24),
            now + timedelta(minutes=1),
        )

    first, second = await asyncio.gather(claim(), claim())
    assert {first.status, second.status} == {"owned", "in_progress"}
    assert first.original_request_id == second.original_request_id
