from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.persistence.bootstrap import DEMO_SCHEMA
from app.providers.mock import MockProvider, MockStep
from tests.fakes import MemoryStore


@pytest.fixture
def memory() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
async def client(memory: MemoryStore, provider: MockProvider) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(), store=memory, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        headers={"X-API-Key": memory.key},
    ) as client:
        yield client


async def test_generation_contract_and_derived_identity(
    client: httpx.AsyncClient, memory: MemoryStore
) -> None:
    response = await client.post(
        "/v1/generate", json={"input": "synthetic input", "metadata": {"tenant_id": "other-tenant"}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "Mock response: synthetic input"
    assert body["estimated_cost_usd"] == "0"
    assert body["request_id"] == response.headers["x-request-id"]
    record = memory.records[UUID(body["request_id"])]
    assert record.tenant_id == memory.principal.tenant_id
    assert record.status == "completed"
    assert body["usage"]["complete"] is True


@pytest.mark.parametrize("headers", [{"X-API-Key": "wrong"}, {"X-API-Key": ""}])
async def test_invalid_credentials(
    client: httpx.AsyncClient, provider: MockProvider, headers: dict[str, str]
) -> None:
    response = await client.post("/v1/generate", json={"input": "hello"}, headers=headers)
    assert response.status_code == 401
    assert provider.invocations == 0
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    "changes",
    [
        {"input": ""},
        {"input": 123},
        {"input": "x" * 100_001},
        {"stream": True},
        {"quality_tier": "unknown"},
        {"cache_mode": "semantic"},
        {"latency_budget_ms": 0},
        {"max_output_tokens": 0},
        {"max_cost_usd": -1},
        {"max_cost_usd": "NaN"},
        {"temperature": -1},
        {"metadata": {str(i): "x" for i in range(21)}},
    ],
)
async def test_request_bounds_before_invocation(
    client: httpx.AsyncClient, provider: MockProvider, changes: dict[str, object]
) -> None:
    response = await client.post("/v1/generate", json={"input": "hello", **changes})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert provider.invocations == 0


@pytest.mark.parametrize("changes", [{"cache_mode": "read_write"}, {"idempotency_key": "a"}])
async def test_later_features_are_not_silently_ignored(
    client: httpx.AsyncClient, provider: MockProvider, changes: dict[str, object]
) -> None:
    response = await client.post("/v1/generate", json={"input": "hello", **changes})
    assert response.status_code == 422
    assert provider.invocations == 0


@pytest.mark.parametrize(
    "schema_fields",
    [
        {"json_schema": DEMO_SCHEMA},
        {"schema_name": "demo-count", "schema_version": "v1"},
    ],
)
async def test_extraction_inline_and_named(
    client: httpx.AsyncClient, schema_fields: dict[str, object]
) -> None:
    response = await client.post("/v1/extract", json={"input": '{"count": 2}', **schema_fields})
    assert response.status_code == 200
    assert response.json()["output"] == {"count": 2}


@pytest.mark.parametrize(
    "schema_fields",
    [
        {},
        {"schema_name": "demo-count"},
        {"schema_name": "demo-count", "schema_version": "v1", "json_schema": DEMO_SCHEMA},
        {"json_schema": {"$ref": "https://example.com/schema"}},
        {"schema_name": "not-authorized", "schema_version": "v1"},
    ],
)
async def test_invalid_schema_selection(
    client: httpx.AsyncClient, provider: MockProvider, schema_fields: dict[str, object]
) -> None:
    response = await client.post("/v1/extract", json={"input": "{}", **schema_fields})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SCHEMA"
    assert provider.invocations == 0


@pytest.mark.parametrize("scenario", ["malformed", "schema_invalid", "refusal", "truncation"])
async def test_invalid_extraction_never_succeeds(memory: MemoryStore, scenario: str) -> None:
    provider = MockProvider([MockStep(scenario, output='{"count": 2}')])
    app = create_app(Settings(), store=memory, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/extract",
            headers={"X-API-Key": memory.key},
            json={"input": '{"count": 2}', "json_schema": DEMO_SCHEMA},
        )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "OUTPUT_VALIDATION_FAILED"
    assert provider.invocations == 1
    assert next(iter(memory.records.values())).status == "failed"


@pytest.mark.parametrize("phase", ["auth", "begin", "finish"])
async def test_critical_storage_failure(
    client: httpx.AsyncClient, memory: MemoryStore, provider: MockProvider, phase: str
) -> None:
    memory.available = phase != "auth"
    memory.fail_begin = phase == "begin"
    memory.fail_finish = phase == "finish"
    response = await client.post("/v1/generate", json={"input": "sensitive synthetic text"})
    assert response.status_code == 503
    assert "sensitive synthetic text" not in response.text
    assert provider.invocations == (1 if phase == "finish" else 0)
    if phase == "finish":
        assert response.json()["error"]["retryable"] is False


async def test_health_without_auth_and_database(
    client: httpx.AsyncClient, memory: MemoryStore
) -> None:
    memory.available = False
    assert (await client.get("/health/live")).status_code == 200
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["components"]["database"] == "unavailable"


async def test_optional_redis_degradation(memory: MemoryStore) -> None:
    app = create_app(Settings(redis_url="redis://127.0.0.1:1"), store=memory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["components"]["redis"] == "degraded_optional"


async def test_chunked_body_limit(memory: MemoryStore, provider: MockProvider) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"input":"'
        yield b"x" * 1024
        yield b'"}'

    app = create_app(Settings(body_limit_bytes=1024), store=memory, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/generate", content=chunks(), headers={"X-API-Key": memory.key}
        )
    assert response.status_code == 413
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert provider.invocations == 0


async def test_malformed_json_error_does_not_echo_body(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/generate", content='{"private-payload"', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert "private-payload" not in response.text


def test_openapi_contract(memory: MemoryStore) -> None:
    schema = create_app(Settings(), store=memory).openapi()
    assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-API-Key"
    for endpoint in ("/v1/generate", "/v1/extract"):
        assert schema["paths"][endpoint]["post"]["security"]
        assert "422" in schema["paths"][endpoint]["post"]["responses"]
    request = schema["components"]["schemas"]["GenerateRequest"]
    assert request["additionalProperties"] is False
    assert request["properties"]["latency_budget_ms"]["maximum"] == 120_000
