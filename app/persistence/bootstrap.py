from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import JSONSchema, Principal
from app.domain.routing import ModelPayload, PolicyPayload, PricingPayload, content_hash
from app.persistence.models import (
    ConfigurationVersion,
    Credential,
    RoutingControl,
    SchemaVersion,
    Tenant,
)
from app.persistence.store import PostgresStore
from app.security.auth import hash_api_key, new_api_key

DEMO_SCHEMA: JSONSchema = {
    "type": "object",
    "properties": {"count": {"type": "integer"}},
    "required": ["count"],
    "additionalProperties": False,
}


def mock_versions() -> list[tuple[str, str, JSONSchema]]:
    versions: list[tuple[str, str, JSONSchema]] = [
        (
            "policy",
            "mock-policy",
            PolicyPayload.model_validate(
                {
                    "candidates": [{"name": "mock-text-v1", "version": "v2", "order": 0}],
                    "weights": {
                        "quality": "1",
                        "affordability": "1",
                        "latency": "1",
                        "health": "1",
                    },
                    "affordability_reference_usd": "1",
                    "minimum_deadline_ms": 0,
                    "attempt_limit": 1,
                }
            ).model_dump(mode="json"),
        ),
        (
            "model",
            "mock-text-v1",
            ModelPayload.model_validate(
                {
                    "provider": "mock",
                    "model": "mock-text-v1",
                    "pricing_version": "v2",
                    "tasks": ["generation", "extraction", "classification", "summarization"],
                    "structured_output": True,
                    "context_limit": 120_000,
                    "output_limit": 16_384,
                    "token_bound": "mock_utf8_bytes",
                    "quality_score": "0.5",
                    "latency_score": "0.5",
                }
            ).model_dump(mode="json"),
        ),
        (
            "pricing",
            "mock-text-v1",
            PricingPayload(input_per_million=0, output_per_million=0).model_dump(mode="json"),
        ),
    ]
    return versions


async def _ensure_mock_configuration(session: AsyncSession) -> None:
    policy_version = None
    for kind, name, payload in mock_versions():
        existing = await session.scalar(
            select(ConfigurationVersion).where(
                ConfigurationVersion.kind == kind,
                ConfigurationVersion.name == name,
                ConfigurationVersion.version == "v2",
            )
        )
        if existing is None:
            existing = ConfigurationVersion(
                kind=kind,
                name=name,
                version="v2",
                payload=payload,
                content_hash=content_hash(payload),
            )
            session.add(existing)
        if kind == "policy":
            policy_version = existing
    await session.flush()
    assert policy_version is not None
    if await session.get(RoutingControl, 1) is None:
        session.add(
            RoutingControl(
                id=1, active_policy_id=policy_version.id, disabled_providers=[], revision=1
            )
        )


async def ensure_local_configuration(store: PostgresStore) -> None:
    """Upgrade local mock configuration without creating or replacing a tenant key."""
    async with store.sessions.begin() as session:
        await _ensure_mock_configuration(session)


async def bootstrap_local(store: PostgresStore, *, role: str = "tenant") -> tuple[str, Principal]:
    """Trusted local CLI bootstrap, not an HTTP provisioning endpoint."""
    if role not in {"tenant", "operator"}:
        raise ValueError("Invalid role")
    key, tenant_id, credential_id = new_api_key(), uuid4(), uuid4()
    async with store.sessions.begin() as session:
        await _ensure_mock_configuration(session)
        session.add(Tenant(id=tenant_id, name="Synthetic local demo"))
        await session.flush()
        session.add(
            Credential(
                id=credential_id,
                tenant_id=tenant_id,
                application_id="local-demo",
                key_hash=hash_api_key(key),
                role=role,
            )
        )
        session.add(
            SchemaVersion(
                tenant_id=tenant_id,
                name="demo-count",
                version="v1",
                payload=DEMO_SCHEMA,
                content_hash=content_hash(DEMO_SCHEMA),
            )
        )
    return key, Principal(tenant_id, "local-demo", credential_id, role)
