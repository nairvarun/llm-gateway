from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx

from app.config import Settings
from app.domain.control import LocalControl
from app.domain.models import FinishReason, TokenUsage
from app.evaluation.worker import run_worker
from app.main import create_app
from app.observability.summary import bounded_window, summary
from app.persistence.bootstrap import bootstrap_local
from app.persistence.models import IdempotencyIngress
from app.persistence.store import PostgresStore
from tests.test_evaluation import RUN


async def test_summary_scopes_application_and_evaluation_traffic(postgres: PostgresStore) -> None:
    key, _ = await bootstrap_local(postgres)
    other_key, _ = await bootstrap_local(postgres)
    app = create_app(Settings(), store=postgres, control=LocalControl())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (
            await client.post("/v1/generate", headers={"X-API-Key": key}, json={"input": "one"})
        ).status_code == 200
        assert (
            await client.post(
                "/v1/generate",
                headers={"X-API-Key": other_key},
                json={"input": "private synthetic"},
            )
        ).status_code == 200
        assert (
            await client.post("/v1/evaluations/runs", headers={"X-API-Key": key}, json=RUN)
        ).status_code == 202
        assert await run_worker(postgres, app.state.gateway_service) == 7
        application = await client.get("/v1/metrics/summary", headers={"X-API-Key": key})
        assert application.status_code == 200
        body = application.json()
        assert body["traffic_kind"] == "application"
        assert body["gateway_requests"] == 1
        assert body["completed_requests"] == 1
        assert body["provider_attempts"] == 1
        assert body["cache_hits"] == 0
        assert (
            body["estimated_fresh_cost_usd"] == "0E-10" or body["estimated_fresh_cost_usd"] == "0"
        )
        evaluation = await client.get(
            "/v1/metrics/summary?traffic_kind=evaluation", headers={"X-API-Key": key}
        )
        assert evaluation.status_code == 200
        assert evaluation.json()["gateway_requests"] >= 5
        assert evaluation.json()["provider_attempts"] >= 5
        another_tenant = await client.get("/v1/metrics/summary", headers={"X-API-Key": other_key})
        assert another_tenant.json()["gateway_requests"] == 1


async def test_summary_rejects_unbounded_or_naive_window(postgres: PostgresStore) -> None:
    key, _ = await bootstrap_local(postgres)
    app = create_app(Settings(), store=postgres, control=LocalControl())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        now = datetime.now(UTC)
        for start, end in (
            (now - timedelta(days=31), now),
            (now + timedelta(days=1), now + timedelta(days=2)),
        ):
            result = await client.get(
                "/v1/metrics/summary",
                params={"starts_at": start.isoformat(), "ends_at": end.isoformat()},
                headers={"X-API-Key": key},
            )
            assert result.status_code == 422
        naive = await client.get(
            "/v1/metrics/summary",
            params={"starts_at": "2026-01-01T00:00:00", "ends_at": now.isoformat()},
            headers={"X-API-Key": key},
        )
        assert naive.status_code == 422


async def test_summary_fresh_cost_tokens_cache_and_replay_have_separate_denominators(
    postgres: PostgresStore,
) -> None:
    _, principal = await bootstrap_local(postgres)
    snapshot = replace(await postgres.snapshot(), estimated_max_cost_usd=Decimal("0.01"))
    source = uuid4()
    dispatch = await postgres.begin(
        principal, source, "/v1/generate", "0" * 64, None, snapshot, Decimal("0.02")
    )
    await postgres.settle_attempt(
        dispatch, "completed", FinishReason.STOP, TokenUsage(2, 3), Decimal("0.0015"), None
    )
    await postgres.complete_request(source, "completed", None)
    await postgres.record_cache_hit(
        principal, uuid4(), "/v1/generate", "0" * 64, None, snapshot, source, Decimal("0.02")
    )
    async with postgres.sessions.begin() as session:
        session.add(
            IdempotencyIngress(
                ingress_request_id=uuid4(),
                tenant_id=principal.tenant_id,
                application_id=principal.application_id,
                endpoint="/v1/generate",
                original_request_id=source,
                outcome="replay",
            )
        )
    start, end = bounded_window(None, None)
    result = await summary(postgres, principal, start, end, "application")
    assert result["gateway_requests"] == 2
    assert result["provider_attempts"] == 1
    assert result["cache_hits"] == 1
    assert result["idempotency_replays"] == 1
    assert result["input_tokens"] == 2
    assert result["output_tokens"] == 3
    assert Decimal(str(result["estimated_fresh_cost_usd"])) == Decimal("0.0015")
    assert result["cache_hit_rate"] == "0.5"
