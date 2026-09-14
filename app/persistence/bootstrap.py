import hashlib
import json
from uuid import uuid4

from sqlalchemy import select

from app.domain.models import JSONSchema, Principal
from app.persistence.models import ConfigurationVersion, Credential, SchemaVersion, Tenant
from app.persistence.store import PostgresStore
from app.security.auth import hash_api_key, new_api_key

DEMO_SCHEMA: JSONSchema = {
    "type": "object",
    "properties": {"count": {"type": "integer"}},
    "required": ["count"],
    "additionalProperties": False,
}


def content_hash(payload: JSONSchema) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def bootstrap_local(store: PostgresStore, *, role: str = "tenant") -> tuple[str, Principal]:
    """Trusted local CLI bootstrap, not an HTTP provisioning endpoint."""
    if role not in {"tenant", "operator"}:
        raise ValueError("Invalid role")
    key, tenant_id, credential_id = new_api_key(), uuid4(), uuid4()
    versions: list[tuple[str, JSONSchema]] = [
        ("policy", {"candidate": "mock-text-v1", "attempt_limit": 1}),
        ("model", {"provider": "mock", "model": "mock-text-v1"}),
        ("pricing", {"input_price_usd": "0", "output_price_usd": "0"}),
    ]
    async with store.sessions.begin() as session:
        for kind, payload in versions:
            existing = await session.scalar(
                select(ConfigurationVersion).where(
                    ConfigurationVersion.kind == kind,
                    ConfigurationVersion.name == "mock",
                    ConfigurationVersion.version == "v1",
                )
            )
            if existing is None:
                session.add(
                    ConfigurationVersion(
                        kind=kind,
                        name="mock",
                        version="v1",
                        payload=payload,
                        content_hash=content_hash(payload),
                    )
                )
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
