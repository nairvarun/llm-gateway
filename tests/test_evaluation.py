from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.domain.control import LocalControl
from app.domain.errors import GatewayError
from app.evaluation.repository import EvaluationRepository
from app.evaluation.worker import run_worker
from app.main import create_app
from app.persistence.bootstrap import bootstrap_local
from app.persistence.models import RequestRecord
from app.persistence.store import PostgresStore
from app.providers.mock import MockProvider

RUN = {
    "dataset_name": "synthetic-gateway",
    "dataset_version": "v1",
    "model_ids": ["mock-text-v1@v2"],
    "policy_version": "mock-policy@v2",
    "threshold_profile": "synthetic-contract@v1",
}


async def test_authorized_run_202_worker_lifecycle_and_tenant_scope(
    postgres: PostgresStore,
) -> None:
    key, principal = await bootstrap_local(postgres)
    other_key, _ = await bootstrap_local(postgres)
    provider = MockProvider()
    app = create_app(Settings(), store=postgres, provider=provider, control=LocalControl())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        unauthenticated = await client.post("/v1/evaluations/runs", json=RUN)
        assert unauthenticated.status_code == 401
        created = await client.post("/v1/evaluations/runs", json=RUN, headers={"X-API-Key": key})
        assert created.status_code == 202
        queued = created.json()
        assert queued["status"] == "queued"
        assert len(queued["cases"]) == 7
        assert len(queued["dataset_sha256"]) == 64
        assert len(queued["code_revision"]) == 64
        run_id = UUID(queued["run_id"])
        forbidden = await client.get(
            f"/v1/evaluations/runs/{run_id}", headers={"X-API-Key": other_key}
        )
        assert forbidden.status_code == 403
        assert await run_worker(postgres, app.state.gateway_service) == 7
        finished = await client.get(f"/v1/evaluations/runs/{run_id}", headers={"X-API-Key": key})
        report = finished.json()
        assert report["status"] == "completed"
        assert {case["status"] for case in report["cases"]} == {"completed", "failed"}
        assert all(case["request_id"] for case in report["cases"])
        assert all("output" not in case for case in report["cases"])
        assert provider.invocations >= 5
        assert await postgres.evidence(principal, UUID(report["cases"][0]["request_id"]))
        async with postgres.sessions() as session:
            requests = (
                await session.scalars(
                    select(RequestRecord).where(RequestRecord.evaluation_run_id == run_id)
                )
            ).all()
        assert requests
        assert all(row.tenant_id == principal.tenant_id for row in requests)
        assert all(row.traffic_kind == "evaluation" for row in requests)
        assert all(not (row.routing_evidence or {}).get("cache_hit") for row in requests)


async def test_interrupted_claim_is_uncertain_and_never_redispatched(
    postgres: PostgresStore,
) -> None:
    key, principal = await bootstrap_local(postgres)
    provider = MockProvider()
    app = create_app(Settings(), store=postgres, provider=provider, control=LocalControl())
    repository = EvaluationRepository(postgres)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        queued = (
            await client.post("/v1/evaluations/runs", json=RUN, headers={"X-API-Key": key})
        ).json()
        claimed = await repository.claim_case()
        assert claimed is not None
        assert claimed.run_id == UUID(queued["run_id"])
        assert await run_worker(postgres, app.state.gateway_service) == 0
        result = (await repository.get(principal, claimed.run_id)).model_dump(mode="json")
        assert result["status"] == "interrupted"
        assert result["error_code"] == "EXECUTION_UNCERTAIN"
        uncertain = [case for case in result["cases"] if case["status"] == "uncertain"]
        assert len(uncertain) == 1
        assert uncertain[0]["request_id"] == str(claimed.request_id)
        assert provider.invocations == 0


async def test_unknown_profile_policy_and_live_model_never_enqueue(postgres: PostgresStore) -> None:
    key, _ = await bootstrap_local(postgres)
    app = create_app(Settings(), store=postgres, control=LocalControl())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for changes in (
            {"threshold_profile": "unapproved@v1"},
            {"policy_version": "wrong@v1"},
            {"model_ids": ["paid-model@v1"]},
            {"dataset_version": "v2"},
        ):
            response = await client.post(
                "/v1/evaluations/runs", json={**RUN, **changes}, headers={"X-API-Key": key}
            )
            assert response.status_code == 422


async def test_generation_review_is_explicit_operator_audited(
    postgres: PostgresStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    key, principal = await bootstrap_local(postgres, role="operator")
    app = create_app(Settings(), store=postgres, control=LocalControl())
    repository = EvaluationRepository(postgres)
    active_before = (await postgres.registry()).policy_id
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        created = await client.post("/v1/evaluations/runs", json=RUN, headers={"X-API-Key": key})
        assert created.status_code == 202
        run_id = UUID(created.json()["run_id"])
        await run_worker(postgres, app.state.gateway_service)
        before = await repository.get(principal, run_id)
        generation = next(case for case in before.cases if case.case_id == "generate-easy")
        assert generation.score is not None
        assert generation.score["human_review_status"] == "pending"
        assert generation.score["output_sha256"] is not None
        pending_gate = await client.get(
            f"/v1/evaluations/runs/{run_id}/gate", headers={"X-API-Key": key}
        )
        assert pending_gate.json()["status"] == "blocked"
        assert pending_gate.json()["reasons"] == ["missing_approved_baseline"]
        await repository.review_generation(principal, run_id, "generate-easy", accepted=True)
        after = await repository.get(principal, run_id)
        reviewed = next(case for case in after.cases if case.case_id == "generate-easy")
        assert reviewed.score is not None
        assert reviewed.score["human_review_status"] == "accepted"
        assert reviewed.score["human_review_credential_id"] == str(principal.credential_id)
        original_get = repository.get

        async def with_held_liability(*args: object) -> object:
            result = await original_get(principal, run_id)
            assert result.report is not None
            measurements = result.report["measurements"]
            assert isinstance(measurements, dict)
            measurements["held_liability_usd"] = "0.10"
            return result

        monkeypatch.setattr(repository, "get", with_held_liability)
        with pytest.raises(GatewayError, match="Baseline evidence is incomplete"):
            await repository.approve_baseline(principal, run_id)
        monkeypatch.setattr(repository, "get", original_get)
        baseline_id = await repository.approve_baseline(principal, run_id)
        assert baseline_id is not None
        with_baseline = await client.get(
            f"/v1/evaluations/runs/{run_id}/gate", headers={"X-API-Key": key}
        )
        assert with_baseline.status_code == 200
        assert with_baseline.json()["status"] == "failed"
        assert "classification_f1_below_threshold" in with_baseline.json()["reasons"]
        assert (await postgres.registry()).policy_id == active_before

        def missing_profile(_name: str) -> None:
            raise ValueError("Profile unavailable")

        with monkeypatch.context() as patch:
            patch.setattr("app.evaluation.repository.load_profile", missing_profile)
            unavailable = await repository.gate(principal, run_id)
            assert unavailable.status == "blocked"
            assert unavailable.reasons == ["threshold_profile_unavailable"]
