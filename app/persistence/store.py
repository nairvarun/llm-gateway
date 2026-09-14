from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.domain.models import (
    Dispatch,
    ExecutionSnapshot,
    FinishReason,
    JSONSchema,
    Principal,
    RequestEvidence,
    StateUnavailable,
    TokenUsage,
)
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


class PostgresStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def authenticate(self, key_hash: str) -> Principal | None:
        try:
            async with self.sessions() as session:
                row = await session.scalar(
                    select(Credential)
                    .join(Tenant)
                    .where(
                        Credential.key_hash == key_hash,
                        Credential.revoked_at.is_(None),
                        Tenant.status == "active",
                    )
                )
                if row is None:
                    return None
                return Principal(row.tenant_id, row.application_id, row.id, row.role)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def ready(self) -> bool:
        try:
            async with self.sessions() as session:
                revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
                if revision != "0001_foundation":
                    return False
            await self.snapshot()
            return True
        except (SQLAlchemyError, OSError, TimeoutError, StateUnavailable):
            return False

    async def snapshot(self) -> ExecutionSnapshot:
        try:
            async with self.sessions() as session:
                rows = (
                    await session.scalars(
                        select(ConfigurationVersion).where(
                            ConfigurationVersion.name == "mock",
                            ConfigurationVersion.version == "v1",
                        )
                    )
                ).all()
                versions = {row.kind: row for row in rows}
                if not {"policy", "model", "pricing"}.issubset(versions):
                    raise StateUnavailable()
                return ExecutionSnapshot(
                    versions["policy"].id,
                    "mock-v1",
                    versions["model"].id,
                    versions["pricing"].id,
                    "mock-free-v1",
                    Decimal("0"),
                    Decimal("0"),
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def schema(self, principal: Principal, name: str, version: str) -> JSONSchema | None:
        try:
            async with self.sessions() as session:
                row = await session.scalar(
                    select(SchemaVersion).where(
                        SchemaVersion.tenant_id == principal.tenant_id,
                        SchemaVersion.name == name,
                        SchemaVersion.version == version,
                    )
                )
                return cast(JSONSchema, row.payload) if row else None
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def begin(
        self,
        principal: Principal,
        request_id: UUID,
        endpoint: str,
        input_hash: str,
        schema_hash: str | None,
        snapshot: ExecutionSnapshot,
    ) -> Dispatch:
        attempt_id = uuid4()
        try:
            async with self.sessions.begin() as session:
                session.add(
                    RequestRecord(
                        id=request_id,
                        tenant_id=principal.tenant_id,
                        application_id=principal.application_id,
                        endpoint=endpoint,
                        input_hash=input_hash,
                        schema_hash=schema_hash,
                        policy_id=snapshot.policy_id,
                        policy_version=snapshot.policy_version,
                    )
                )
                await session.flush()
                session.add(
                    Attempt(
                        id=attempt_id,
                        request_id=request_id,
                        provider="mock",
                        model="mock-text-v1",
                        model_id=snapshot.model_id,
                        pricing_id=snapshot.pricing_id,
                    )
                )
            return Dispatch(request_id, attempt_id)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def finish(
        self,
        dispatch: Dispatch,
        status: str,
        finish_reason: FinishReason | None,
        usage: TokenUsage,
        cost: Decimal,
        error_code: str | None,
        error_class: str | None,
    ) -> None:
        try:
            async with self.sessions.begin() as session:
                request = await session.scalar(
                    select(RequestRecord)
                    .where(RequestRecord.id == dispatch.request_id)
                    .with_for_update()
                )
                attempt = await session.get(Attempt, dispatch.attempt_id)
                if request is None or attempt is None or attempt.request_id != request.id:
                    raise StateUnavailable()
                if request.status != "in_progress":
                    # Terminal accounting is immutable through this operation.
                    return
                now = datetime.now(UTC)
                request.status, request.error_code, request.completed_at = status, error_code, now
                attempt.outcome, attempt.completed_at = status, now
                attempt.finish_reason = finish_reason.value if finish_reason else None
                attempt.error_class = error_class
                session.add(
                    UsageEvent(
                        attempt_id=attempt.id,
                        request_id=request.id,
                        tenant_id=request.tenant_id,
                        pricing_id=attempt.pricing_id,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        usage_status=usage.status,
                        estimated_cost_usd=cost,
                    )
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def evidence(self, principal: Principal, request_id: UUID) -> RequestEvidence | None:
        try:
            async with self.sessions() as session:
                query = select(RequestRecord).where(RequestRecord.id == request_id)
                if principal.role != "operator":
                    query = query.where(RequestRecord.tenant_id == principal.tenant_id)
                row = await session.scalar(query)
                return (
                    RequestEvidence(
                        row.id,
                        row.tenant_id,
                        row.application_id,
                        row.status,
                        row.policy_version,
                        row.error_code,
                    )
                    if row
                    else None
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def revoke(self, principal: Principal, credential_id: UUID) -> None:
        from app.security.auth import require_operator

        require_operator(principal)
        try:
            async with self.sessions.begin() as session:
                credential = await session.get(Credential, credential_id)
                if credential is None:
                    return
                credential.revoked_at = datetime.now(UTC)
                session.add(
                    AuditEvent(
                        actor_id=principal.credential_id,
                        action="credential.revoked",
                        target_id=str(credential_id),
                    )
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error
