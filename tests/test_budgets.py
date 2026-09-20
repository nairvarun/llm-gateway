import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.domain.errors import GatewayError
from app.domain.models import BudgetExceeded, FinishReason, TokenUsage
from app.main import create_app
from app.persistence.bootstrap import bootstrap_local
from app.persistence.models import (
    Attempt,
    AuditEvent,
    BudgetBucket,
    IdempotencyRecord,
    RequestRecord,
    SpendReservation,
    Tenant,
    UsageEvent,
)
from app.persistence.store import PostgresStore
from app.providers.mock import MockProvider

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("constrained_period", ["day", "month"])
async def test_competing_replicas_cannot_over_admit_tenant_bucket(
    postgres: PostgresStore,
    constrained_period: str,
) -> None:
    _, principal = await bootstrap_local(postgres)
    async with postgres.sessions.begin() as session:
        tenant = await session.get(Tenant, principal.tenant_id)
        assert tenant is not None
        tenant.daily_budget_usd = (
            Decimal("0.0015") if constrained_period == "day" else Decimal("0.01")
        )
        tenant.monthly_budget_usd = (
            Decimal("0.0015") if constrained_period == "month" else Decimal("0.01")
        )
    snapshot = replace(await postgres.snapshot(), estimated_max_cost_usd=Decimal("0.001"))

    async def dispatch() -> object:
        return await postgres.begin(
            principal,
            uuid4(),
            "/v1/generate",
            "0" * 64,
            None,
            snapshot,
            Decimal("0.01"),
        )

    results = await asyncio.gather(dispatch(), dispatch(), return_exceptions=True)
    assert sum(isinstance(item, BudgetExceeded) for item in results) == 1
    async with postgres.sessions() as session:
        bucket = await session.scalar(
            select(BudgetBucket).where(
                BudgetBucket.tenant_id == principal.tenant_id,
                BudgetBucket.period == constrained_period,
            )
        )
        assert bucket is not None and bucket.held_usd == Decimal("0.001")


async def test_known_usage_releases_difference_and_duplicate_settlement_is_once(
    postgres: PostgresStore,
) -> None:
    _, principal = await bootstrap_local(postgres)
    async with postgres.sessions.begin() as session:
        tenant = await session.get(Tenant, principal.tenant_id)
        assert tenant is not None
        tenant.daily_budget_usd = Decimal("0.0025")
    snapshot = replace(await postgres.snapshot(), estimated_max_cost_usd=Decimal("0.002"))
    request_id = uuid4()
    first = await postgres.begin(
        principal, request_id, "/v1/generate", "0" * 64, None, snapshot, Decimal("0.003")
    )
    await postgres.settle_attempt(
        first, "failed", None, TokenUsage(1, 1), Decimal("0.0005"), "server"
    )
    await postgres.settle_attempt(
        first, "failed", None, TokenUsage(1, 1), Decimal("0.0005"), "server"
    )
    second = await postgres.add_attempt(request_id, 2, snapshot)
    with pytest.raises(BudgetExceeded):
        await postgres.add_attempt(request_id, 3, snapshot)
    await postgres.settle_attempt(
        second, "uncertain", None, TokenUsage(None, None, "unknown"), Decimal("0.002"), "timeout"
    )
    async with postgres.sessions() as session:
        day = await session.scalar(
            select(BudgetBucket).where(
                BudgetBucket.tenant_id == principal.tenant_id,
                BudgetBucket.period == "day",
            )
        )
        assert day is not None
        assert day.committed_usd == Decimal("0.0005")
        assert day.held_usd == Decimal("0.002")
        assert await session.scalar(
            select(UsageEvent).where(UsageEvent.attempt_id == first.attempt_id)
        )
        first_reservation = await session.get(SpendReservation, first.attempt_id)
        second_reservation = await session.get(SpendReservation, second.attempt_id)
        assert first_reservation is not None and first_reservation.state == "reconciled"
        assert second_reservation is not None and second_reservation.state == "held"


async def test_original_month_bucket_survives_utc_day_rollover(
    postgres: PostgresStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, principal = await bootstrap_local(postgres)
    async with postgres.sessions.begin() as session:
        tenant = await session.get(Tenant, principal.tenant_id)
        assert tenant is not None
        tenant.daily_budget_usd = Decimal("0.001")
        tenant.monthly_budget_usd = Decimal("0.002")
    snapshot = replace(await postgres.snapshot(), estimated_max_cost_usd=Decimal("0.001"))
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    month = today.replace(day=1)
    monkeypatch.setattr("app.persistence.store.utc_period_starts", lambda _at: (today, month))
    first = await postgres.begin(
        principal, uuid4(), "/v1/generate", "0" * 64, None, snapshot, Decimal("0.001")
    )
    monkeypatch.setattr(
        "app.persistence.store.utc_period_starts",
        lambda _at: (today + timedelta(days=1), month),
    )
    second = await postgres.begin(
        principal, uuid4(), "/v1/generate", "0" * 64, None, snapshot, Decimal("0.001")
    )
    await postgres.settle_attempt(
        first, "completed", FinishReason.STOP, TokenUsage(1, 1), Decimal("0.0004"), None
    )
    async with postgres.sessions() as session:
        buckets = (
            await session.scalars(
                select(BudgetBucket)
                .where(BudgetBucket.tenant_id == principal.tenant_id)
                .order_by(BudgetBucket.period, BudgetBucket.starts_at)
            )
        ).all()
        days = [item for item in buckets if item.period == "day"]
        month_bucket = next(item for item in buckets if item.period == "month")
        assert len(days) == 2
        assert days[0].committed_usd == Decimal("0.0004")
        assert days[1].held_usd == Decimal("0.001")
        assert month_bucket.committed_usd == Decimal("0.0004")
        assert month_bucket.held_usd == Decimal("0.001")
        assert second.attempt_id != first.attempt_id


async def test_month_rollover_retains_original_period_liability(
    postgres: PostgresStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, principal = await bootstrap_local(postgres)
    async with postgres.sessions.begin() as session:
        tenant = await session.get(Tenant, principal.tenant_id)
        assert tenant is not None
        tenant.monthly_budget_usd = Decimal("0.001")
    snapshot = replace(await postgres.snapshot(), estimated_max_cost_usd=Decimal("0.001"))
    september = datetime(2026, 9, 1, tzinfo=UTC)
    october = datetime(2026, 10, 1, tzinfo=UTC)
    monkeypatch.setattr(
        "app.persistence.store.utc_period_starts",
        lambda _at: (september + timedelta(days=29), september),
    )
    first = await postgres.begin(
        principal, uuid4(), "/v1/generate", "0" * 64, None, snapshot, Decimal("0.001")
    )
    monkeypatch.setattr("app.persistence.store.utc_period_starts", lambda _at: (october, october))
    await postgres.begin(
        principal, uuid4(), "/v1/generate", "0" * 64, None, snapshot, Decimal("0.001")
    )
    await postgres.settle_attempt(
        first, "failed", None, TokenUsage(1, 1), Decimal("0.0002"), "server"
    )
    async with postgres.sessions() as session:
        months = (
            await session.scalars(
                select(BudgetBucket)
                .where(
                    BudgetBucket.tenant_id == principal.tenant_id,
                    BudgetBucket.period == "month",
                )
                .order_by(BudgetBucket.starts_at)
            )
        ).all()
        assert len(months) == 2
        assert (months[0].committed_usd, months[0].held_usd) == (Decimal("0.0002"), Decimal("0"))
        assert (months[1].committed_usd, months[1].held_usd) == (Decimal("0"), Decimal("0.001"))


async def test_unknown_hold_requires_audited_conservative_recovery(
    postgres: PostgresStore,
) -> None:
    _, tenant = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    snapshot = replace(await postgres.snapshot(), estimated_max_cost_usd=Decimal("0.002"))
    dispatch = await postgres.begin(
        tenant, uuid4(), "/v1/generate", "0" * 64, None, snapshot, Decimal("0.01")
    )
    await postgres.settle_attempt(
        dispatch, "uncertain", None, TokenUsage(None, None, "unknown"), Decimal("0.002"), "timeout"
    )
    assert await postgres.conservative_recover_attempt(operator, dispatch.attempt_id)
    assert not await postgres.conservative_recover_attempt(operator, dispatch.attempt_id)
    async with postgres.sessions() as session:
        reservation = await session.get(SpendReservation, dispatch.attempt_id)
        assert reservation is not None
        assert reservation.state == "conservative"
        assert reservation.charged_usd == Decimal("0.002")
        day = await session.scalar(
            select(BudgetBucket).where(
                BudgetBucket.tenant_id == tenant.tenant_id, BudgetBucket.period == "day"
            )
        )
        assert day is not None and day.held_usd == 0 and day.committed_usd == Decimal("0.002")
        events = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "spend.conservative_recovery")
            )
        ).all()
        assert len(events) == 1
        usage = (
            await session.scalars(
                select(UsageEvent).where(UsageEvent.attempt_id == dispatch.attempt_id)
            )
        ).all()
        assert len(usage) == 1 and usage[0].usage_status == "unknown"


async def test_stale_post_dispatch_recovery_preserves_unknown_evidence(
    postgres: PostgresStore,
) -> None:
    _, tenant = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    snapshot = replace(await postgres.snapshot(), estimated_max_cost_usd=Decimal("0.001"))
    dispatch = await postgres.begin(
        tenant, uuid4(), "/v1/generate", "0" * 64, None, snapshot, Decimal("0.01")
    )
    with pytest.raises(ValueError):
        await postgres.conservative_recover_attempt(operator, dispatch.attempt_id)
    async with postgres.sessions.begin() as session:
        attempt = await session.get(Attempt, dispatch.attempt_id)
        assert attempt is not None
        attempt.started_at = datetime.now(UTC) - timedelta(minutes=6)
    assert await postgres.conservative_recover_attempt(operator, dispatch.attempt_id)
    async with postgres.sessions() as session:
        request = await session.get(RequestRecord, dispatch.request_id)
        attempt = await session.get(Attempt, dispatch.attempt_id)
        assert request is not None and request.status == "uncertain"
        assert attempt is not None and attempt.outcome == "uncertain"
        assert await session.scalar(select(UsageEvent).where(UsageEvent.attempt_id == attempt.id))


async def test_reported_cost_overrun_is_recorded_and_disables_unsafe_provider(
    postgres: PostgresStore,
) -> None:
    _, tenant = await bootstrap_local(postgres)
    snapshot = replace(await postgres.snapshot(), estimated_max_cost_usd=Decimal("0.001"))
    dispatch = await postgres.begin(
        tenant, uuid4(), "/v1/generate", "0" * 64, None, snapshot, Decimal("0.01")
    )
    await postgres.settle_attempt(
        dispatch, "completed", FinishReason.STOP, TokenUsage(2, 2), Decimal("0.002"), None
    )
    assert not await postgres.provider_enabled("mock")
    async with postgres.sessions() as session:
        reservation = await session.get(SpendReservation, dispatch.attempt_id)
        assert reservation is not None
        assert reservation.overrun_usd == Decimal("0.001")
        assert reservation.charged_usd == Decimal("0.002")
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "spend.overrun_provider_disabled")
        )
        assert audit is not None


async def test_spend_query_is_scoped_to_authenticated_tenant(postgres: PostgresStore) -> None:
    key, first = await bootstrap_local(postgres)
    other_key, second = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    snapshot = replace(await postgres.snapshot(), estimated_max_cost_usd=Decimal("0.002"))
    dispatch = await postgres.begin(
        first, uuid4(), "/v1/generate", "0" * 64, None, snapshot, Decimal("0.01")
    )
    await postgres.settle_attempt(
        dispatch, "completed", FinishReason.STOP, TokenUsage(2, 3), Decimal("0.0005"), None
    )
    with pytest.raises(GatewayError) as denied:
        await postgres.spend_summary(second, first.tenant_id)
    assert denied.value.code == "FORBIDDEN"
    assert (await postgres.spend_summary(operator, first.tenant_id)).day.committed_usd == Decimal(
        "0.0005"
    )
    app = create_app(Settings(), store=postgres, provider=MockProvider())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        owner = await client.get("/v1/spend", headers={"X-API-Key": key})
        other = await client.get("/v1/spend", headers={"X-API-Key": other_key})
    assert owner.status_code == other.status_code == 200
    assert owner.json()["tenant_id"] == str(first.tenant_id)
    assert owner.json()["day"]["committed_usd"] == "0.0005000000"
    assert other.json()["tenant_id"] == str(second.tenant_id)
    assert other.json()["day"]["committed_usd"] == "0"


async def test_retention_purges_expired_content_but_preserves_unknown_holds(
    postgres: PostgresStore,
) -> None:
    _, tenant = await bootstrap_local(postgres)
    _, operator = await bootstrap_local(postgres, role="operator")
    now = datetime.now(UTC)
    old = now - timedelta(days=31)
    snapshot = replace(await postgres.snapshot(), estimated_max_cost_usd=Decimal("0.001"))
    settled = await postgres.begin(
        tenant, uuid4(), "/v1/generate", "0" * 64, None, snapshot, Decimal("0.01")
    )
    await postgres.settle_attempt(
        settled, "completed", FinishReason.STOP, TokenUsage(1, 1), Decimal("0.0002"), None
    )
    await postgres.complete_request(settled.request_id, "completed", None)
    unknown = await postgres.begin(
        tenant, uuid4(), "/v1/generate", "0" * 64, None, snapshot, Decimal("0.01")
    )
    await postgres.settle_attempt(
        unknown, "uncertain", None, TokenUsage(None, None, "unknown"), Decimal("0.001"), "timeout"
    )
    await postgres.complete_request(unknown.request_id, "uncertain", "EXECUTION_UNCERTAIN")
    async with postgres.sessions.begin() as session:
        for request_id in (settled.request_id, unknown.request_id):
            record = await session.get(RequestRecord, request_id)
            assert record is not None
            record.completed_at = old
        session.add(
            IdempotencyRecord(
                tenant_id=tenant.tenant_id,
                endpoint="/v1/generate",
                key_hash="a" * 64,
                fingerprint="b" * 64,
                original_request_id=settled.request_id,
                status="completed",
                encrypted_result=b"protected-result",
                expires_at=now - timedelta(seconds=1),
                owner_expires_at=old,
            )
        )
    with pytest.raises(GatewayError):
        await postgres.purge_retention(tenant, 30, now=now)
    counts = await postgres.purge_retention(operator, 30, now=now)
    assert counts["replay_records"] == 1
    assert counts["requests"] == 1
    async with postgres.sessions() as session:
        assert await session.get(RequestRecord, settled.request_id) is None
        assert await session.get(RequestRecord, unknown.request_id) is not None
        assert (
            await session.get(IdempotencyRecord, (tenant.tenant_id, "/v1/generate", "a" * 64))
            is None
        )
        hold = await session.get(SpendReservation, unknown.attempt_id)
        assert hold is not None and hold.state == "held"
