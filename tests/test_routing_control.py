from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.domain.errors import GatewayError
from app.domain.routing import ModelPayload, PolicyPayload, PricingPayload
from app.main import create_app
from app.persistence.bootstrap import bootstrap_local
from app.persistence.models import AuditEvent, ConfigurationVersion, RequestRecord, RoutingControl
from app.persistence.store import PostgresStore
from app.providers.mock import MockProvider

pytestmark = pytest.mark.integration


async def test_operator_publish_activate_rollback_and_immutable_inflight_snapshot(
    postgres: PostgresStore,
) -> None:
    _, tenant = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    original = await postgres.registry()
    policy = original.policy.model_dump(mode="json")
    policy["weights"]["quality"] = "2"
    with pytest.raises(GatewayError):
        await postgres.publish_configuration(tenant, "policy", "mock-policy", "v3", policy)
    with pytest.raises(GatewayError):
        await postgres.activate_policy(tenant, "mock-policy", "v2")
    with pytest.raises(GatewayError):
        await postgres.set_provider_disabled(tenant, "mock", True)
    await postgres.publish_configuration(operator, "policy", "mock-policy", "v3", policy)
    assert (await postgres.registry()).policy_version == "v2"
    await postgres.activate_policy(operator, "mock-policy", "v3")
    assert (await postgres.registry()).policy_version == "v3"
    assert original.policy_version == "v2"
    assert original.models[0].pricing_id == (await postgres.registry()).models[0].pricing_id
    await postgres.activate_policy(operator, "mock-policy", "v2", rollback=True)
    assert (await postgres.registry()).policy_version == "v2"
    async with postgres.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 3
        control = await session.get(RoutingControl, 1)
        assert control is not None and control.revision == 3
        rows = (
            await session.scalars(
                select(ConfigurationVersion).where(ConfigurationVersion.kind == "policy")
            )
        ).all()
        assert len(rows) == 2


async def test_invalid_policy_dependencies_cannot_activate(postgres: PostgresStore) -> None:
    _, operator = await bootstrap_local(postgres, role="operator")
    invalid = PolicyPayload.model_validate(
        {
            "candidates": [{"name": "missing-model", "version": "v1", "order": 0}],
            "weights": {"quality": "1", "affordability": "0", "latency": "0", "health": "0"},
            "affordability_reference_usd": "1",
            "minimum_deadline_ms": 0,
            "attempt_limit": 1,
        }
    )
    await postgres.publish_configuration(
        operator, "policy", "invalid", "v1", invalid.model_dump(mode="json")
    )
    with pytest.raises(ValueError):
        await postgres.activate_policy(operator, "invalid", "v1")
    assert (await postgres.registry()).policy_name == "mock-policy"


async def test_model_and_pricing_versions_are_pinned(postgres: PostgresStore) -> None:
    _, operator = await bootstrap_local(postgres, role="operator")
    before = await postgres.registry()
    profile = ModelPayload.model_validate(
        {
            "provider": "mock",
            "model": "mock-new",
            "pricing_version": "v1",
            "tasks": ["generation"],
            "structured_output": False,
            "context_limit": 5000,
            "output_limit": 1000,
            "token_bound": "mock_utf8_bytes",
            "quality_score": "0.7",
            "latency_score": "0.5",
        }
    )
    await postgres.publish_configuration(
        operator,
        "pricing",
        "mock-new",
        "v1",
        PricingPayload(
            input_per_million=Decimal("0.1"), output_per_million=Decimal("0.2")
        ).model_dump(mode="json"),
    )
    await postgres.publish_configuration(
        operator, "model", "mock-new", "v1", profile.model_dump(mode="json")
    )
    policy = before.policy.model_dump(mode="json")
    policy["candidates"] = [{"name": "mock-new", "version": "v1", "order": 0}]
    await postgres.publish_configuration(operator, "policy", "new", "v1", policy)
    await postgres.activate_policy(operator, "new", "v1")
    after = await postgres.registry()
    assert after.models[0].payload.model == "mock-new"
    assert after.models[0].pricing.input_per_million == Decimal("0.1")
    assert before.models[0].payload.model == "mock-text-v1"
    assert before.models[0].pricing.input_per_million == Decimal("0")


async def test_provider_disable_blocks_new_api_attempt_and_audits(postgres: PostgresStore) -> None:
    key, principal = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    provider = MockProvider()
    app = create_app(Settings(), store=postgres, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/v1/generate", headers={"X-API-Key": key}, json={"input": "synthetic"}
        )
        assert first.status_code == 200
        first_id = UUID(first.json()["request_id"])
        original_evidence = await postgres.evidence(principal, first_id)
        assert original_evidence is not None
        assert original_evidence.routing_evidence is not None
        candidates = cast(list[dict[str, Any]], original_evidence.routing_evidence["candidates"])
        assert candidates[0]["reasons"] == []
        assert first.json()["routing"] == original_evidence.routing_evidence
        await postgres.set_provider_disabled(operator, "mock", True)
        second = await client.post(
            "/v1/generate", headers={"X-API-Key": key}, json={"input": "synthetic"}
        )
        assert second.status_code == 503
        assert second.json()["error"]["code"] == "NO_ELIGIBLE_MODEL"
        assert "disabled" in second.json()["error"]["message"]
        rejected = await postgres.evidence(principal, UUID(second.json()["request_id"]))
        assert rejected is not None and rejected.status == "failed"
        assert rejected.routing_evidence is not None
        rejected_candidates = cast(list[dict[str, Any]], rejected.routing_evidence["candidates"])
        assert rejected_candidates[0]["reasons"] == ["disabled"]
        assert provider.invocations == 1
        assert (await postgres.evidence(principal, first_id)) == original_evidence
        await postgres.set_provider_disabled(operator, "mock", False)
        third = await client.post(
            "/v1/generate", headers={"X-API-Key": key}, json={"input": "synthetic"}
        )
        assert third.status_code == 200
    async with postgres.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(RequestRecord)) == 3


async def test_disablement_after_snapshot_prevents_dispatch(
    postgres: PostgresStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    key, principal = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    original_begin = postgres.begin

    async def begin_then_disable(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        dispatch = await original_begin(*args, **kwargs)  # type: ignore[arg-type]
        await postgres.set_provider_disabled(operator, "mock", True)
        return dispatch

    monkeypatch.setattr(postgres, "begin", begin_then_disable)
    provider = MockProvider()
    app = create_app(Settings(), store=postgres, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/generate", headers={"X-API-Key": key}, json={"input": "synthetic"}
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NO_ELIGIBLE_MODEL"
    assert provider.invocations == 0
    evidence = await postgres.evidence(principal, UUID(response.json()["request_id"]))
    assert evidence is not None and evidence.status == "failed"
    assert evidence.routing_evidence is not None
    assert evidence.routing_evidence["dispatch_exclusion"] == "provider_disabled"
