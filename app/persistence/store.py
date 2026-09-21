import hashlib
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.domain.models import (
    BudgetExceeded,
    Dispatch,
    ExecutionSnapshot,
    FinishReason,
    IdempotencyClaim,
    JSONSchema,
    JSONValue,
    Principal,
    RequestEvidence,
    SpendBucketView,
    SpendSummary,
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
    BudgetBucket,
    CacheKeyGeneration,
    CacheNamespace,
    ConfigurationVersion,
    Credential,
    IdempotencyIngress,
    IdempotencyRecord,
    RequestRecord,
    RoutingControl,
    SchemaVersion,
    SpendReservation,
    Tenant,
    UsageEvent,
)
from app.usage.buckets import liability, utc_period_starts


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
                if revision != "0009_ingress_scope":
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

    async def cache_approved(self, principal: Principal) -> bool:
        try:
            async with self.sessions() as session:
                tenant = await session.get(Tenant, principal.tenant_id)
                return bool(
                    tenant is not None and tenant.status == "active" and tenant.cache_approved
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def spend_summary(self, principal: Principal, tenant_id: UUID) -> SpendSummary:
        from app.domain.errors import GatewayError

        if principal.role != "operator" and tenant_id != principal.tenant_id:
            raise GatewayError("FORBIDDEN", "Spend summary is unavailable in this scope.", 403)
        try:
            async with self.sessions() as session:
                tenant = await session.get(Tenant, tenant_id)
                if tenant is None:
                    raise GatewayError(
                        "FORBIDDEN", "Spend summary is unavailable in this scope.", 403
                    )
                now = await session.scalar(select(func.now()))
                if now is None:
                    raise StateUnavailable()
                day_start, month_start = utc_period_starts(now)
                values: list[SpendBucketView] = []
                for period, start, limit in (
                    ("day", day_start, tenant.daily_budget_usd),
                    ("month", month_start, tenant.monthly_budget_usd),
                ):
                    bucket = await session.get(BudgetBucket, (tenant_id, period, start))
                    values.append(
                        SpendBucketView(
                            start,
                            bucket.limit_usd if bucket is not None else limit,
                            bucket.committed_usd if bucket is not None else Decimal("0"),
                            bucket.held_usd if bucket is not None else Decimal("0"),
                        )
                    )
                return SpendSummary(tenant_id, values[0], values[1])
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def purge_retention(
        self, principal: Principal, metadata_days: int, *, now: datetime | None = None
    ) -> dict[str, int]:
        """Bounded operator cleanup; unresolved holds are never silently discarded."""
        from app.security.auth import require_operator

        require_operator(principal)
        if not 1 <= metadata_days <= 365:
            raise ValueError("Invalid metadata retention")
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(days=metadata_days)
        current_month = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        try:
            async with self.sessions.begin() as session:
                replay = await session.execute(
                    delete(IdempotencyRecord)
                    .where(IdempotencyRecord.expires_at <= current)
                    .returning(IdempotencyRecord.key_hash)
                )
                ingress = await session.execute(
                    delete(IdempotencyIngress)
                    .where(IdempotencyIngress.created_at < cutoff)
                    .returning(IdempotencyIngress.ingress_request_id)
                )
                old = (
                    await session.scalars(
                        select(RequestRecord.id)
                        .where(
                            RequestRecord.completed_at < cutoff,
                            RequestRecord.status.in_(["completed", "failed", "uncertain"]),
                            ~select(SpendReservation.attempt_id)
                            .where(
                                SpendReservation.request_id == RequestRecord.id,
                                SpendReservation.state == "held",
                            )
                            .exists(),
                        )
                        .order_by(RequestRecord.completed_at)
                        .limit(1000)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
                if old:
                    await session.execute(delete(UsageEvent).where(UsageEvent.request_id.in_(old)))
                    await session.execute(
                        delete(SpendReservation).where(SpendReservation.request_id.in_(old))
                    )
                    await session.execute(delete(Attempt).where(Attempt.request_id.in_(old)))
                    await session.execute(delete(RequestRecord).where(RequestRecord.id.in_(old)))
                audit = await session.execute(
                    delete(AuditEvent)
                    .where(AuditEvent.created_at < cutoff)
                    .returning(AuditEvent.id)
                )
                # A prior-month bucket may still anchor a current 30-day record.
                buckets = (
                    await session.scalars(
                        select(BudgetBucket)
                        .where(BudgetBucket.starts_at < cutoff)
                        .limit(1000)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
                purged_buckets = 0
                for bucket in buckets:
                    if bucket.period == "month" and bucket.starts_at >= current_month:
                        continue
                    reference = await session.scalar(
                        select(SpendReservation.attempt_id)
                        .where(
                            SpendReservation.tenant_id == bucket.tenant_id,
                            (
                                SpendReservation.day_start == bucket.starts_at
                                if bucket.period == "day"
                                else SpendReservation.month_start == bucket.starts_at
                            ),
                        )
                        .limit(1)
                    )
                    if reference is None:
                        await session.delete(bucket)
                        purged_buckets += 1
                return {
                    "replay_records": len(replay.all()),
                    "ingress_records": len(ingress.all()),
                    "requests": len(old),
                    "audit_records": len(audit.all()),
                    "budget_buckets": purged_buckets,
                }
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def cache_generations(self, principal: Principal, key_hash: str) -> tuple[int, int]:
        if re.fullmatch(r"[0-9a-f]{64}", key_hash) is None:
            raise ValueError("Invalid cache key hash")
        try:
            async with self.sessions.begin() as session:
                identity = (principal.tenant_id, principal.application_id)
                await session.execute(
                    insert(CacheNamespace)
                    .values(tenant_id=identity[0], application_id=identity[1], generation=1)
                    .on_conflict_do_nothing(index_elements=["tenant_id", "application_id"])
                )
                await session.execute(
                    insert(CacheKeyGeneration)
                    .values(
                        tenant_id=identity[0],
                        application_id=identity[1],
                        key_hash=key_hash,
                        generation=1,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["tenant_id", "application_id", "key_hash"]
                    )
                )
                namespace = await session.get(CacheNamespace, identity)
                exact = await session.get(CacheKeyGeneration, (*identity, key_hash))
                if namespace is None or exact is None:
                    raise StateUnavailable()
                return namespace.generation, exact.generation
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def set_cache_approval(
        self, principal: Principal, tenant_id: UUID, approved: bool
    ) -> None:
        from app.security.auth import require_operator

        require_operator(principal)
        try:
            async with self.sessions.begin() as session:
                tenant = await session.scalar(
                    select(Tenant).where(Tenant.id == tenant_id).with_for_update()
                )
                if tenant is None:
                    raise ValueError("Tenant is unavailable")
                if tenant.cache_approved == approved:
                    return
                tenant.cache_approved = approved
                namespaces = (
                    await session.scalars(
                        select(CacheNamespace)
                        .where(CacheNamespace.tenant_id == tenant_id)
                        .with_for_update()
                    )
                ).all()
                for namespace in namespaces:
                    namespace.generation += 1
                    namespace.updated_at = datetime.now(UTC)
                session.add(
                    AuditEvent(
                        actor_id=principal.credential_id,
                        action="cache.approval_enabled" if approved else "cache.approval_disabled",
                        target_id=str(tenant_id),
                    )
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def invalidate_cache_namespace(
        self, principal: Principal, tenant_id: UUID, application_id: str
    ) -> int:
        from app.security.auth import require_operator

        require_operator(principal)
        if not 1 <= len(application_id) <= 100:
            raise ValueError("Invalid application identity")
        try:
            async with self.sessions.begin() as session:
                identity = (tenant_id, application_id)
                await session.execute(
                    insert(CacheNamespace)
                    .values(tenant_id=tenant_id, application_id=application_id, generation=1)
                    .on_conflict_do_nothing(index_elements=["tenant_id", "application_id"])
                )
                row = await session.scalar(
                    select(CacheNamespace)
                    .where(
                        CacheNamespace.tenant_id == identity[0],
                        CacheNamespace.application_id == identity[1],
                    )
                    .with_for_update()
                )
                if row is None:
                    raise StateUnavailable()
                row.generation += 1
                row.updated_at = datetime.now(UTC)
                session.add(
                    AuditEvent(
                        actor_id=principal.credential_id,
                        action="cache.namespace_invalidated",
                        target_id=hashlib.sha256(
                            f"{tenant_id}:{application_id}".encode()
                        ).hexdigest(),
                    )
                )
                return row.generation
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def invalidate_cache_exact(
        self, principal: Principal, tenant_id: UUID, application_id: str, key_hash: str
    ) -> int:
        from app.security.auth import require_operator

        require_operator(principal)
        if not 1 <= len(application_id) <= 100 or re.fullmatch(r"[0-9a-f]{64}", key_hash) is None:
            raise ValueError("Invalid cache identity")
        try:
            async with self.sessions.begin() as session:
                await session.execute(
                    insert(CacheKeyGeneration)
                    .values(
                        tenant_id=tenant_id,
                        application_id=application_id,
                        key_hash=key_hash,
                        generation=1,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["tenant_id", "application_id", "key_hash"]
                    )
                )
                row = await session.scalar(
                    select(CacheKeyGeneration)
                    .where(
                        CacheKeyGeneration.tenant_id == tenant_id,
                        CacheKeyGeneration.application_id == application_id,
                        CacheKeyGeneration.key_hash == key_hash,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise StateUnavailable()
                row.generation += 1
                row.updated_at = datetime.now(UTC)
                session.add(
                    AuditEvent(
                        actor_id=principal.credential_id,
                        action="cache.exact_invalidated",
                        target_id=key_hash,
                    )
                )
                return row.generation
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def claim_idempotency(
        self,
        principal: Principal,
        endpoint: str,
        key_hash: str,
        fingerprint: str,
        ingress_id: UUID,
        expires_at: datetime,
        owner_expires_at: datetime,
    ) -> IdempotencyClaim:
        try:
            async with self.sessions.begin() as session:
                identity = (principal.tenant_id, endpoint, key_hash)
                inserted = await session.scalar(
                    insert(IdempotencyRecord)
                    .values(
                        tenant_id=identity[0],
                        endpoint=identity[1],
                        key_hash=identity[2],
                        fingerprint=fingerprint,
                        original_request_id=ingress_id,
                        status="in_progress",
                        expires_at=expires_at,
                        owner_expires_at=owner_expires_at,
                    )
                    .on_conflict_do_nothing(index_elements=["tenant_id", "endpoint", "key_hash"])
                    .returning(IdempotencyRecord.original_request_id)
                )
                if inserted is not None:
                    return IdempotencyClaim("owned", ingress_id)
                row = await session.scalar(
                    select(IdempotencyRecord)
                    .where(
                        IdempotencyRecord.tenant_id == identity[0],
                        IdempotencyRecord.endpoint == identity[1],
                        IdempotencyRecord.key_hash == identity[2],
                    )
                    .with_for_update()
                )
                if row is None:
                    raise StateUnavailable()
                now = datetime.now(UTC)
                if row.expires_at <= now:
                    row.fingerprint = fingerprint
                    row.original_request_id = ingress_id
                    row.status = "in_progress"
                    row.encrypted_result = None
                    row.expires_at = expires_at
                    row.owner_expires_at = owner_expires_at
                    row.updated_at = now
                    return IdempotencyClaim("owned", ingress_id)
                if row.fingerprint != fingerprint:
                    outcome = "conflict"
                elif row.status == "in_progress" and row.owner_expires_at <= now:
                    outcome = "uncertain"
                    row.status = "uncertain"
                    row.updated_at = now
                    request = await session.get(RequestRecord, row.original_request_id)
                    if request is not None and request.status == "in_progress":
                        request.status = "uncertain"
                        request.error_code = "EXECUTION_UNCERTAIN"
                        request.completed_at = now
                        attempts = (
                            await session.scalars(
                                select(Attempt).where(
                                    Attempt.request_id == request.id,
                                    Attempt.outcome == "dispatched",
                                )
                            )
                        ).all()
                        for attempt in attempts:
                            attempt.outcome = "uncertain"
                            attempt.error_class = "worker_lost"
                            attempt.completed_at = now
                            session.add(
                                UsageEvent(
                                    attempt_id=attempt.id,
                                    request_id=request.id,
                                    tenant_id=request.tenant_id,
                                    pricing_id=attempt.pricing_id,
                                    input_tokens=None,
                                    output_tokens=None,
                                    usage_status="unknown",
                                    estimated_cost_usd=attempt.reserved_upper_cost_usd,
                                )
                            )
                elif row.status == "in_progress":
                    outcome = "in_progress"
                elif row.status in {"completed", "failed"} and row.encrypted_result is not None:
                    outcome = "replay"
                else:
                    outcome = "uncertain"
                session.add(
                    IdempotencyIngress(
                        ingress_request_id=ingress_id,
                        tenant_id=principal.tenant_id,
                        application_id=principal.application_id,
                        endpoint=endpoint,
                        original_request_id=row.original_request_id,
                        outcome=outcome,
                    )
                )
                return IdempotencyClaim(outcome, row.original_request_id, row.encrypted_result)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def seal_idempotency(
        self,
        tenant_id: UUID,
        endpoint: str,
        key_hash: str,
        original_request_id: UUID,
        status: str,
        encrypted_result: bytes,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("Only completed or failed executions can be replayed")
        try:
            async with self.sessions.begin() as session:
                row = await session.scalar(
                    select(IdempotencyRecord)
                    .where(
                        IdempotencyRecord.tenant_id == tenant_id,
                        IdempotencyRecord.endpoint == endpoint,
                        IdempotencyRecord.key_hash == key_hash,
                    )
                    .with_for_update()
                )
                request = await session.get(RequestRecord, original_request_id)
                if (
                    row is None
                    or row.original_request_id != original_request_id
                    or row.status != "in_progress"
                    or request is None
                    or request.status != status
                ):
                    raise StateUnavailable()
                row.status = status
                row.encrypted_result = encrypted_result
                row.updated_at = datetime.now(UTC)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def mark_idempotency_uncertain(self, original_request_id: UUID) -> None:
        try:
            async with self.sessions.begin() as session:
                row = await session.scalar(
                    select(IdempotencyRecord)
                    .where(IdempotencyRecord.original_request_id == original_request_id)
                    .with_for_update()
                )
                if row is not None and row.status == "in_progress":
                    row.status = "uncertain"
                    row.updated_at = datetime.now(UTC)
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
        max_cost_usd: Decimal = Decimal("1"),
    ) -> Dispatch:
        attempt_id = uuid4()
        try:
            async with self.sessions.begin() as session:
                request = RequestRecord(
                    id=request_id,
                    tenant_id=principal.tenant_id,
                    application_id=principal.application_id,
                    traffic_kind=principal.traffic_kind,
                    evaluation_run_id=principal.evaluation_run_id,
                    endpoint=endpoint,
                    input_hash=input_hash,
                    schema_hash=schema_hash,
                    policy_id=snapshot.policy_id,
                    policy_version=snapshot.policy_version,
                    routing_evidence=snapshot.routing_evidence,
                    max_cost_usd=max_cost_usd,
                )
                session.add(request)
                await session.flush()
                attempt = Attempt(
                    id=attempt_id,
                    request_id=request_id,
                    provider=snapshot.provider,
                    model=snapshot.model,
                    model_id=snapshot.model_id,
                    pricing_id=snapshot.pricing_id,
                    reserved_upper_cost_usd=snapshot.estimated_max_cost_usd,
                )
                session.add(attempt)
                await session.flush()
                await self._reserve(session, request, attempt)
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
        error_code: str = "NO_ELIGIBLE_MODEL",
    ) -> None:
        try:
            async with self.sessions.begin() as session:
                session.add(
                    RequestRecord(
                        id=request_id,
                        tenant_id=principal.tenant_id,
                        application_id=principal.application_id,
                        traffic_kind=principal.traffic_kind,
                        evaluation_run_id=principal.evaluation_run_id,
                        endpoint=endpoint,
                        input_hash=input_hash,
                        schema_hash=schema_hash,
                        policy_id=policy_id,
                        policy_version=policy_version,
                        routing_evidence=routing_evidence,
                        status="failed",
                        error_code=error_code,
                        completed_at=datetime.now(UTC),
                    )
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def record_cache_hit(
        self,
        principal: Principal,
        request_id: UUID,
        endpoint: str,
        input_hash: str,
        schema_hash: str | None,
        snapshot: ExecutionSnapshot,
        source_request_id: UUID,
        max_cost_usd: Decimal,
    ) -> None:
        try:
            async with self.sessions.begin() as session:
                evidence = {
                    **(snapshot.routing_evidence or {}),
                    "cache_hit": True,
                    "source_request_id": str(source_request_id),
                }
                session.add(
                    RequestRecord(
                        id=request_id,
                        tenant_id=principal.tenant_id,
                        application_id=principal.application_id,
                        traffic_kind=principal.traffic_kind,
                        evaluation_run_id=principal.evaluation_run_id,
                        endpoint=endpoint,
                        input_hash=input_hash,
                        schema_hash=schema_hash,
                        policy_id=snapshot.policy_id,
                        policy_version=snapshot.policy_version,
                        routing_evidence=evidence,
                        max_cost_usd=max_cost_usd,
                        status="completed",
                        completed_at=datetime.now(UTC),
                    )
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def add_attempt(
        self, request_id: UUID, number: int, snapshot: ExecutionSnapshot
    ) -> Dispatch:
        if number < 2:
            raise ValueError("Subsequent attempt number must be at least two")
        attempt_id = uuid4()
        try:
            async with self.sessions.begin() as session:
                request = await session.scalar(
                    select(RequestRecord).where(RequestRecord.id == request_id).with_for_update()
                )
                if request is None or request.status != "in_progress":
                    raise StateUnavailable()
                if (
                    request.policy_id != snapshot.policy_id
                    or request.policy_version != snapshot.policy_version
                ):
                    raise StateUnavailable()
                maximum = await session.scalar(
                    select(func.max(Attempt.number)).where(Attempt.request_id == request_id)
                )
                if maximum is None or number != maximum + 1:
                    raise ValueError("Attempt number is not sequential")
                attempt = Attempt(
                    id=attempt_id,
                    request_id=request_id,
                    number=number,
                    provider=snapshot.provider,
                    model=snapshot.model,
                    model_id=snapshot.model_id,
                    pricing_id=snapshot.pricing_id,
                    reserved_upper_cost_usd=snapshot.estimated_max_cost_usd,
                )
                session.add(attempt)
                await session.flush()
                await self._reserve(session, request, attempt)
            return Dispatch(request_id, attempt_id)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def _reserve(
        self, session: AsyncSession, request: RequestRecord, attempt: Attempt
    ) -> None:
        upper = attempt.reserved_upper_cost_usd
        if upper < 0 or request.max_cost_usd is None or request.max_cost_usd <= 0:
            raise StateUnavailable()
        prior = (
            await session.scalars(
                select(SpendReservation).where(SpendReservation.request_id == request.id)
            )
        ).all()
        already = sum(
            (liability(item.state, item.reserved_usd, item.charged_usd) for item in prior),
            Decimal("0"),
        )
        if already + upper > request.max_cost_usd:
            raise BudgetExceeded()
        tenant = await session.get(Tenant, request.tenant_id)
        if tenant is None or tenant.status != "active":
            raise StateUnavailable()
        database_now = await session.scalar(select(func.now()))
        if database_now is None:
            raise StateUnavailable()
        day_start, month_start = utc_period_starts(database_now)
        for period, start, limit in (
            ("day", day_start, tenant.daily_budget_usd),
            ("month", month_start, tenant.monthly_budget_usd),
        ):
            await session.execute(
                insert(BudgetBucket)
                .values(
                    tenant_id=request.tenant_id,
                    period=period,
                    starts_at=start,
                    limit_usd=limit,
                    held_usd=Decimal("0"),
                    committed_usd=Decimal("0"),
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "period", "starts_at"])
            )
            bucket = await session.scalar(
                select(BudgetBucket)
                .where(
                    BudgetBucket.tenant_id == request.tenant_id,
                    BudgetBucket.period == period,
                    BudgetBucket.starts_at == start,
                )
                .with_for_update()
            )
            if bucket is None:
                raise StateUnavailable()
            if bucket.committed_usd + bucket.held_usd + upper > bucket.limit_usd:
                raise BudgetExceeded()
            bucket.held_usd += upper
        session.add(
            SpendReservation(
                attempt_id=attempt.id,
                request_id=request.id,
                tenant_id=request.tenant_id,
                day_start=day_start,
                month_start=month_start,
                reserved_usd=upper,
                charged_usd=Decimal("0"),
                state="held",
            )
        )

    async def settle_attempt(
        self,
        dispatch: Dispatch,
        status: str,
        finish_reason: FinishReason | None,
        usage: TokenUsage,
        cost: Decimal,
        error_class: str | None,
    ) -> None:
        if status not in {"completed", "failed", "uncertain", "not_dispatched"}:
            raise ValueError("Invalid attempt terminal status")
        try:
            async with self.sessions.begin() as session:
                attempt = await session.scalar(
                    select(Attempt).where(Attempt.id == dispatch.attempt_id).with_for_update()
                )
                if attempt is None or attempt.request_id != dispatch.request_id:
                    raise StateUnavailable()
                if attempt.outcome != "dispatched":
                    return
                request = await session.get(RequestRecord, attempt.request_id)
                if request is None or request.status != "in_progress":
                    raise StateUnavailable()
                await self._reconcile(session, attempt, status, usage, cost)
                attempt.outcome = status
                attempt.completed_at = datetime.now(UTC)
                attempt.finish_reason = finish_reason.value if finish_reason else None
                attempt.error_class = error_class
                if error_class == "provider_disabled" and request.routing_evidence is not None:
                    request.routing_evidence = {
                        **request.routing_evidence,
                        "dispatch_exclusion": "provider_disabled",
                    }
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

    async def complete_request(self, request_id: UUID, status: str, error_code: str | None) -> None:
        if status not in {"completed", "failed", "uncertain"}:
            raise ValueError("Invalid request terminal status")
        try:
            async with self.sessions.begin() as session:
                request = await session.scalar(
                    select(RequestRecord).where(RequestRecord.id == request_id).with_for_update()
                )
                if request is None:
                    raise StateUnavailable()
                if request.status != "in_progress":
                    return
                unfinished = await session.scalar(
                    select(func.count())
                    .select_from(Attempt)
                    .where(Attempt.request_id == request_id, Attempt.outcome == "dispatched")
                )
                if unfinished:
                    raise StateUnavailable()
                request.status = status
                request.error_code = error_code
                request.completed_at = datetime.now(UTC)
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
                await self._reconcile(session, attempt, status, usage, cost)
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

    async def _reconcile(
        self,
        session: AsyncSession,
        attempt: Attempt,
        status: str,
        usage: TokenUsage,
        cost: Decimal,
    ) -> None:
        reservation = await session.scalar(
            select(SpendReservation)
            .where(SpendReservation.attempt_id == attempt.id)
            .with_for_update()
        )
        if reservation is None or reservation.state != "held":
            raise StateUnavailable()
        known = usage.input_tokens is not None and usage.output_tokens is not None
        if status == "not_dispatched":
            next_state, charge = "released", Decimal("0")
        elif known:
            next_state, charge = "reconciled", cost
        else:
            return  # The original UTC bucket retains the conservative hold.
        if charge < 0:
            raise StateUnavailable()
        await self._move_hold(session, reservation, charge)
        reservation.state = next_state
        reservation.charged_usd = charge
        reservation.overrun_usd = max(Decimal("0"), charge - reservation.reserved_usd)
        reservation.reconciled_at = datetime.now(UTC)
        if reservation.overrun_usd > 0:
            control = await session.scalar(
                select(RoutingControl).where(RoutingControl.id == 1).with_for_update()
            )
            if control is None:
                raise StateUnavailable()
            if attempt.provider not in control.disabled_providers:
                control.disabled_providers = [*control.disabled_providers, attempt.provider]
                control.revision += 1
            session.add(
                AuditEvent(
                    actor_id=None,
                    action="spend.overrun_provider_disabled",
                    target_id=str(attempt.id),
                )
            )

    async def _move_hold(
        self, session: AsyncSession, reservation: SpendReservation, charge: Decimal
    ) -> None:
        for period, start in (("day", reservation.day_start), ("month", reservation.month_start)):
            bucket = await session.scalar(
                select(BudgetBucket)
                .where(
                    BudgetBucket.tenant_id == reservation.tenant_id,
                    BudgetBucket.period == period,
                    BudgetBucket.starts_at == start,
                )
                .with_for_update()
            )
            if bucket is None or bucket.held_usd < reservation.reserved_usd:
                raise StateUnavailable()
            bucket.held_usd -= reservation.reserved_usd
            bucket.committed_usd += charge

    async def conservative_recover_attempt(self, principal: Principal, attempt_id: UUID) -> bool:
        """Audited manual resolution of a known-unknown attempt; never redispatches."""
        from app.security.auth import require_operator

        require_operator(principal)
        try:
            async with self.sessions.begin() as session:
                attempt = await session.scalar(
                    select(Attempt).where(Attempt.id == attempt_id).with_for_update()
                )
                if attempt is None:
                    raise ValueError("Attempt is unavailable")
                reservation = await session.scalar(
                    select(SpendReservation)
                    .where(SpendReservation.attempt_id == attempt_id)
                    .with_for_update()
                )
                if reservation is None:
                    raise StateUnavailable()
                if reservation.state != "held":
                    return False
                if attempt.outcome == "dispatched":
                    # An active or very recent worker may still settle this attempt.
                    if datetime.now(UTC) - attempt.started_at < timedelta(minutes=5):
                        raise ValueError("Attempt is not old enough for crash recovery")
                    attempt.outcome = "uncertain"
                    attempt.error_class = "worker_lost"
                    attempt.completed_at = datetime.now(UTC)
                    request = await session.get(RequestRecord, attempt.request_id)
                    if request is not None and request.status == "in_progress":
                        request.status = "uncertain"
                        request.error_code = "EXECUTION_UNCERTAIN"
                        request.completed_at = datetime.now(UTC)
                    keyed = await session.scalar(
                        select(IdempotencyRecord)
                        .where(IdempotencyRecord.original_request_id == attempt.request_id)
                        .with_for_update()
                    )
                    if keyed is not None and keyed.status == "in_progress":
                        keyed.status = "uncertain"
                        keyed.updated_at = datetime.now(UTC)
                existing_event = await session.scalar(
                    select(UsageEvent.id).where(UsageEvent.attempt_id == attempt_id)
                )
                if existing_event is None:
                    session.add(
                        UsageEvent(
                            attempt_id=attempt.id,
                            request_id=attempt.request_id,
                            tenant_id=reservation.tenant_id,
                            pricing_id=attempt.pricing_id,
                            input_tokens=None,
                            output_tokens=None,
                            usage_status="unknown",
                            estimated_cost_usd=reservation.reserved_usd,
                        )
                    )
                await self._move_hold(session, reservation, reservation.reserved_usd)
                reservation.state = "conservative"
                reservation.charged_usd = reservation.reserved_usd
                reservation.reconciled_at = datetime.now(UTC)
                session.add(
                    AuditEvent(
                        actor_id=principal.credential_id,
                        action="spend.conservative_recovery",
                        target_id=str(attempt.id),
                    )
                )
                return True
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
