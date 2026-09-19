from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.domain.models import (
    Dispatch,
    ExecutionSnapshot,
    FinishReason,
    JSONSchema,
    JSONValue,
    Principal,
    RequestEvidence,
    StateUnavailable,
    TokenUsage,
)
from app.domain.routing import (
    ModelPayload,
    ModelSnapshot,
    PolicyPayload,
    PricingPayload,
    RegistrySnapshot,
    content_hash,
)
from app.persistence.models import (
    Attempt,
    AuditEvent,
    ConfigurationVersion,
    Credential,
    RequestRecord,
    RoutingControl,
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
                if revision != "0002_routing_control":
                    return False
            await self.registry()
            return True
        except (SQLAlchemyError, OSError, TimeoutError, StateUnavailable):
            return False

    async def snapshot(self) -> ExecutionSnapshot:
        registry = await self.registry()
        model = registry.models[0]
        return ExecutionSnapshot(
            registry.policy_id,
            registry.policy_version,
            model.id,
            model.pricing_id,
            model.pricing_version,
            model.pricing.input_per_million,
            model.pricing.output_per_million,
        )

    async def registry(self) -> RegistrySnapshot:
        try:
            async with self.sessions() as session:
                control = await session.get(RoutingControl, 1)
                if control is None:
                    raise StateUnavailable()
                policy_row = await session.get(ConfigurationVersion, control.active_policy_id)
                return await self._resolve_registry(session, policy_row, control.disabled_providers)
        except (SQLAlchemyError, OSError, TimeoutError, ValidationError, ValueError) as error:
            raise StateUnavailable() from error

    async def _resolve_registry(
        self,
        session: AsyncSession,
        policy_row: ConfigurationVersion | None,
        disabled_providers: list[str],
    ) -> RegistrySnapshot:
        if policy_row is None or policy_row.kind != "policy":
            raise ValueError("Policy version is unavailable")
        if policy_row.content_hash != content_hash(policy_row.payload):
            raise ValueError("Policy hash is invalid")
        policy = PolicyPayload.model_validate(policy_row.payload)
        model_rows = (
            await session.scalars(
                select(ConfigurationVersion).where(ConfigurationVersion.kind == "model")
            )
        ).all()
        price_rows = (
            await session.scalars(
                select(ConfigurationVersion).where(ConfigurationVersion.kind == "pricing")
            )
        ).all()
        model_map = {(row.name, row.version): row for row in model_rows}
        price_map = {(row.name, row.version): row for row in price_rows}
        models: list[ModelSnapshot] = []
        for candidate in policy.candidates:
            row = model_map.get((candidate.name, candidate.version))
            if row is None or row.content_hash != content_hash(row.payload):
                raise ValueError("Model version is unavailable or invalid")
            profile = ModelPayload.model_validate(row.payload)
            if profile.model != row.name:
                raise ValueError("Model identity is invalid")
            price = price_map.get((row.name, profile.pricing_version))
            if price is None or price.content_hash != content_hash(price.payload):
                raise ValueError("Pricing version is unavailable or invalid")
            models.append(
                ModelSnapshot(
                    row.id,
                    row.version,
                    profile,
                    price.id,
                    price.version,
                    PricingPayload.model_validate(price.payload),
                )
            )
        if not isinstance(disabled_providers, list) or any(
            not isinstance(provider, str) for provider in disabled_providers
        ):
            raise ValueError("Disabled-provider state is invalid")
        return RegistrySnapshot(
            policy_row.id,
            policy_row.name,
            policy_row.version,
            policy,
            tuple(models),
            frozenset(disabled_providers),
        )

    async def provider_enabled(self, provider: str) -> bool:
        try:
            async with self.sessions() as session:
                control = await session.get(RoutingControl, 1)
                if control is None:
                    raise StateUnavailable()
                return provider not in control.disabled_providers
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
                        routing_evidence=snapshot.routing_evidence,
                    )
                )
                await session.flush()
                session.add(
                    Attempt(
                        id=attempt_id,
                        request_id=request_id,
                        provider=snapshot.provider,
                        model=snapshot.model,
                        model_id=snapshot.model_id,
                        pricing_id=snapshot.pricing_id,
                    )
                )
            return Dispatch(request_id, attempt_id)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def reject_routing(
        self,
        principal: Principal,
        request_id: UUID,
        endpoint: str,
        input_hash: str,
        schema_hash: str | None,
        policy_id: UUID,
        policy_version: str,
        routing_evidence: dict[str, JSONValue],
    ) -> None:
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
                        policy_id=policy_id,
                        policy_version=policy_version,
                        routing_evidence=routing_evidence,
                        status="failed",
                        error_code="NO_ELIGIBLE_MODEL",
                        completed_at=datetime.now(UTC),
                    )
                )
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
                if error_class == "provider_disabled" and request.routing_evidence is not None:
                    request.routing_evidence = {
                        **request.routing_evidence,
                        "dispatch_exclusion": "provider_disabled",
                    }
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
                        row.routing_evidence,
                    )
                    if row
                    else None
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def publish_configuration(
        self, principal: Principal, kind: str, name: str, version: str, payload: dict[str, object]
    ) -> UUID:
        from app.security.auth import require_operator

        require_operator(principal)
        if kind not in {"policy", "model", "pricing"} or not name or not version:
            raise ValueError("Invalid configuration identity")
        if len(name) > 100 or len(version) > 100:
            raise ValueError("Configuration identity is too long")
        validated: PolicyPayload | ModelPayload | PricingPayload
        if kind == "policy":
            validated = PolicyPayload.model_validate(payload)
        elif kind == "model":
            validated = ModelPayload.model_validate(payload)
            if validated.model != name:
                raise ValueError("Model name does not match its profile")
        else:
            validated = PricingPayload.model_validate(payload)
        normalized = validated.model_dump(mode="json")
        try:
            async with self.sessions.begin() as session:
                row = ConfigurationVersion(
                    kind=kind,
                    name=name,
                    version=version,
                    payload=normalized,
                    content_hash=content_hash(normalized),
                )
                session.add(row)
                await session.flush()
                session.add(
                    AuditEvent(
                        actor_id=principal.credential_id,
                        action=f"configuration.{kind}.published",
                        target_id=str(row.id),
                    )
                )
                return row.id
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def activate_policy(
        self, principal: Principal, name: str, version: str, *, rollback: bool = False
    ) -> None:
        from app.security.auth import require_operator

        require_operator(principal)
        try:
            async with self.sessions.begin() as session:
                control = await session.scalar(
                    select(RoutingControl).where(RoutingControl.id == 1).with_for_update()
                )
                if control is None:
                    raise StateUnavailable()
                policy_row = await session.scalar(
                    select(ConfigurationVersion).where(
                        ConfigurationVersion.kind == "policy",
                        ConfigurationVersion.name == name,
                        ConfigurationVersion.version == version,
                    )
                )
                await self._resolve_registry(session, policy_row, control.disabled_providers)
                assert policy_row is not None
                control.active_policy_id = policy_row.id
                control.revision += 1
                session.add(
                    AuditEvent(
                        actor_id=principal.credential_id,
                        action="policy.rolled_back" if rollback else "policy.activated",
                        target_id=str(policy_row.id),
                    )
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def set_provider_disabled(
        self, principal: Principal, provider: str, disabled: bool
    ) -> None:
        from app.security.auth import require_operator

        require_operator(principal)
        if not provider or len(provider) > 100:
            raise ValueError("Invalid provider name")
        try:
            async with self.sessions.begin() as session:
                exists = await session.scalar(
                    select(ConfigurationVersion.id)
                    .where(
                        ConfigurationVersion.kind == "model",
                        ConfigurationVersion.payload["provider"].astext == provider,
                    )
                    .limit(1)
                )
                if exists is None:
                    raise ValueError("Provider is not registered")
                control = await session.scalar(
                    select(RoutingControl).where(RoutingControl.id == 1).with_for_update()
                )
                if control is None:
                    raise StateUnavailable()
                values = set(control.disabled_providers)
                if disabled:
                    values.add(provider)
                else:
                    values.discard(provider)
                control.disabled_providers = sorted(values)
                control.revision += 1
                session.add(
                    AuditEvent(
                        actor_id=principal.credential_id,
                        action="provider.disabled" if disabled else "provider.enabled",
                        target_id=provider,
                    )
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
