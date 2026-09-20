from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import Settings
from app.domain.errors import GatewayError
from app.domain.models import FinishReason, TokenUsage
from app.main import create_app
from app.persistence.bootstrap import bootstrap_local
from app.persistence.models import (
    Attempt,
    AuditEvent,
    ConfigurationVersion,
    Credential,
    RequestRecord,
    SchemaVersion,
    Tenant,
    UsageEvent,
)
from app.persistence.store import PostgresStore
from app.providers.mock import MockProvider, MockStep
from app.security.auth import hash_api_key, require_operator

pytestmark = pytest.mark.integration


async def test_migration_and_immutable_versions(postgres: PostgresStore) -> None:
    await bootstrap_local(postgres)
    assert await postgres.ready()
    async with postgres.sessions() as session:
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        assert revision == "0004_replay_recovery_index"
    async with postgres.sessions.begin() as session:
        with pytest.raises(DBAPIError):
            await session.execute(text("UPDATE configuration_versions SET version = 'v2'"))
        await session.rollback()


async def test_hashed_auth_revocation_and_operator_scope(postgres: PostgresStore) -> None:
    key, tenant = await bootstrap_local(postgres)
    operator_key, operator = await bootstrap_local(postgres, role="operator")
    assert await postgres.authenticate(hash_api_key(key)) == tenant
    assert await postgres.authenticate(hash_api_key("wrong")) is None
    assert await postgres.authenticate(hash_api_key(operator_key)) == operator
    with pytest.raises(GatewayError):
        require_operator(tenant)
    with pytest.raises(GatewayError):
        await postgres.revoke(tenant, operator.credential_id)
    await postgres.revoke(operator, tenant.credential_id)
    assert await postgres.authenticate(hash_api_key(key)) is None
    async with postgres.sessions() as session:
        stored = await session.get(Credential, tenant.credential_id)
        assert stored is not None and stored.key_hash == hash_api_key(key)
        assert stored.key_hash != key
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 1


async def test_cross_tenant_schema_and_evidence(postgres: PostgresStore) -> None:
    _, first = await bootstrap_local(postgres)
    _, second = await bootstrap_local(postgres)
    snapshot = await postgres.snapshot()
    dispatch = await postgres.begin(first, uuid4(), "/v1/generate", "0" * 64, None, snapshot)
    assert await postgres.evidence(first, dispatch.request_id) is not None
    assert await postgres.evidence(second, dispatch.request_id) is None
    async with postgres.sessions.begin() as session:
        session.add(
            SchemaVersion(
                tenant_id=first.tenant_id,
                name="first-only",
                version="v1",
                content_hash="0" * 64,
                payload={"type": "string"},
            )
        )
    assert await postgres.schema(first, "first-only", "v1") == {"type": "string"}
    assert await postgres.schema(second, "first-only", "v1") is None


async def test_http_revoked_and_disabled_tenants(postgres: PostgresStore) -> None:
    key, principal = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    provider = MockProvider()
    app = create_app(Settings(), store=postgres, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        async with postgres.sessions.begin() as session:
            tenant = await session.get(Tenant, principal.tenant_id)
            assert tenant is not None
            tenant.status = "disabled"
        response = await client.post(
            "/v1/generate", headers={"X-API-Key": key}, json={"input": "test"}
        )
        assert response.status_code == 401
        await postgres.revoke(operator, principal.credential_id)
        async with postgres.sessions.begin() as session:
            tenant = await session.get(Tenant, principal.tenant_id)
            assert tenant is not None
            tenant.status = "active"
        response = await client.post(
            "/v1/generate", headers={"X-API-Key": key}, json={"input": "test"}
        )
        assert response.status_code == 401
    assert provider.invocations == 0


@pytest.mark.parametrize("table", ["requests", "usage_events"])
async def test_critical_transaction_failure_never_dispatches_or_claims_success(
    postgres: PostgresStore, table: str
) -> None:
    key, _ = await bootstrap_local(postgres)
    async with postgres.engine.begin() as connection:
        await connection.execute(
            text("""
            CREATE FUNCTION reject_test_write() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Injected test write failure'; END;
            $$ LANGUAGE plpgsql
        """)
        )
        await connection.execute(
            text(f"""
            CREATE TRIGGER rejected_test_write BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_test_write()
        """)
        )
    provider = MockProvider()
    app = create_app(Settings(), store=postgres, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/generate", headers={"X-API-Key": key}, json={"input": "private synthetic test"}
        )
    assert response.status_code == 503
    assert provider.invocations == (0 if table == "requests" else 1)
    assert "private synthetic test" not in response.text
    if table == "usage_events":
        assert response.json()["error"]["retryable"] is False
        async with postgres.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Attempt)) == 1
            record = await session.scalar(select(RequestRecord))
            assert record is not None and record.status == "in_progress"


async def test_usage_event_uniqueness_and_idempotent_terminal_recording(
    postgres: PostgresStore,
) -> None:
    _, principal = await bootstrap_local(postgres)
    snapshot = await postgres.snapshot()
    dispatch = await postgres.begin(principal, uuid4(), "/v1/generate", "0" * 64, None, snapshot)
    args = (
        dispatch,
        "completed",
        FinishReason.STOP,
        TokenUsage(2, 3, "synthetic"),
        Decimal("0"),
        None,
        None,
    )
    await postgres.finish(*args)
    await postgres.finish(*args)
    async with postgres.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(UsageEvent)) == 1
    async with postgres.sessions.begin() as session:
        session.add(
            UsageEvent(
                attempt_id=dispatch.attempt_id,
                request_id=dispatch.request_id,
                tenant_id=principal.tenant_id,
                pricing_id=snapshot.pricing_id,
                input_tokens=2,
                output_tokens=3,
                usage_status="synthetic",
                estimated_cost_usd=Decimal("0"),
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_separate_attempt_records_preserve_usage_before_request_terminal(
    postgres: PostgresStore,
) -> None:
    _, principal = await bootstrap_local(postgres)
    snapshot = await postgres.snapshot()
    request_id = uuid4()
    first = await postgres.begin(principal, request_id, "/v1/generate", "0" * 64, None, snapshot)
    await postgres.settle_attempt(
        first,
        "uncertain",
        None,
        TokenUsage(None, None, "unknown"),
        Decimal("0"),
        "timeout",
    )
    await postgres.settle_attempt(
        first,
        "uncertain",
        None,
        TokenUsage(None, None, "unknown"),
        Decimal("0"),
        "timeout",
    )
    second = await postgres.add_attempt(request_id, 2, snapshot)
    with pytest.raises(ValueError):
        await postgres.add_attempt(request_id, 4, snapshot)
    await postgres.settle_attempt(
        second,
        "completed",
        FinishReason.STOP,
        TokenUsage(2, 3, "synthetic"),
        Decimal("0"),
        None,
    )
    await postgres.complete_request(request_id, "completed", None)
    async with postgres.sessions() as session:
        attempts = (
            await session.scalars(
                select(Attempt).where(Attempt.request_id == request_id).order_by(Attempt.number)
            )
        ).all()
        usage = (
            await session.scalars(select(UsageEvent).where(UsageEvent.request_id == request_id))
        ).all()
        assert [item.outcome for item in attempts] == ["uncertain", "completed"]
        assert len(usage) == 2
        assert usage[0].usage_status == "unknown"
        assert usage[1].usage_status == "synthetic"


@pytest.mark.parametrize("scenario", ["success", "malformed", "credential", "missing_usage"])
async def test_real_database_api_records_and_privacy(
    postgres: PostgresStore, scenario: str
) -> None:
    key, principal = await bootstrap_local(postgres)
    provider = MockProvider([MockStep(scenario)])
    app = create_app(Settings(), store=postgres, provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/extract",
            headers={"X-API-Key": key},
            json={"input": '{"count": 2}', "schema_name": "demo-count", "schema_version": "v1"},
        )
    assert response.status_code == (
        200
        if scenario in {"success", "missing_usage"}
        else 503
        if scenario == "credential"
        else 502
    )
    request_id = UUID(response.json()["request_id"])
    evidence = await postgres.evidence(principal, request_id)
    assert evidence is not None
    assert evidence.status == ("completed" if response.status_code == 200 else "failed")
    async with postgres.sessions() as session:
        request = await session.get(RequestRecord, request_id)
        attempt = await session.scalar(select(Attempt).where(Attempt.request_id == request_id))
        usage = await session.scalar(select(UsageEvent).where(UsageEvent.request_id == request_id))
        assert request and attempt and usage
        assert usage.pricing_id == attempt.pricing_id
        assert (
            usage.input_tokens is None
            if scenario in {"credential", "missing_usage"}
            else usage.input_tokens is not None
        )
        assert "input" not in RequestRecord.__table__.columns.keys()
        assert "output" not in RequestRecord.__table__.columns.keys()
        assert request.input_hash != '{"count": 2}'
        assert len(request.input_hash) == 64
        assert len((await session.scalars(select(ConfigurationVersion))).all()) == 3
