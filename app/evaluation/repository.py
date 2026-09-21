from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from math import ceil
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas import EvaluationCaseResponse, EvaluationRunResponse
from app.domain.errors import GatewayError
from app.domain.models import Principal, StateUnavailable
from app.evaluation.datasets import DatasetManifest, case_hash
from app.evaluation.gates import GateResult, compare
from app.evaluation.profiles import ThresholdProfile, load_profile
from app.evaluation.scoring import SCORER_VERSION, aggregate
from app.persistence.models import (
    AuditEvent,
    EvaluationBaseline,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRun,
    RequestRecord,
    SpendReservation,
    UsageEvent,
)
from app.persistence.store import PostgresStore


@dataclass(frozen=True)
class CaseWork:
    row_id: UUID
    run_id: UUID
    request_id: UUID
    case_id: str
    model_id: str
    dataset_name: str
    dataset_version: str
    dataset_sha256: str
    case_sha256: str
    policy_version: str
    threshold_profile: str
    threshold_sha256: str
    code_revision: str
    tenant_id: UUID
    application_id: str
    credential_id: UUID


class EvaluationRepository:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    async def enqueue(
        self,
        principal: Principal,
        manifest: DatasetManifest,
        model_ids: list[str],
        policy_id: UUID,
        policy_version: str,
        profile: ThresholdProfile,
        code_revision: str,
        pricing_versions: dict[str, str],
    ) -> EvaluationRunResponse:
        try:
            async with self.store.sessions.begin() as session:
                await session.execute(
                    insert(EvaluationDataset)
                    .values(
                        id=uuid4(),
                        name=manifest.name,
                        version=manifest.version,
                        content_sha256=manifest.content_sha256,
                        case_count=len(manifest.cases),
                        provenance=manifest.provenance,
                        approved=True,
                    )
                    .on_conflict_do_nothing(index_elements=["name", "version"])
                )
                dataset = await session.scalar(
                    select(EvaluationDataset).where(
                        EvaluationDataset.name == manifest.name,
                        EvaluationDataset.version == manifest.version,
                    )
                )
                if (
                    dataset is None
                    or not dataset.approved
                    or dataset.content_sha256 != manifest.content_sha256
                ):
                    raise GatewayError(
                        "INVALID_REQUEST", "Dataset version is unavailable or changed.", 422
                    )
                run = EvaluationRun(
                    id=uuid4(),
                    tenant_id=principal.tenant_id,
                    application_id=principal.application_id,
                    credential_id=principal.credential_id,
                    dataset_id=dataset.id,
                    dataset_sha256=manifest.content_sha256,
                    model_ids=model_ids,
                    policy_id=policy_id,
                    policy_version=policy_version,
                    threshold_profile=profile.identity,
                    threshold_sha256=profile.content_sha256,
                    code_revision=code_revision,
                    prompt_version=f"{manifest.name}@{manifest.version}",
                    evaluator_version=SCORER_VERSION,
                    pricing_versions=pricing_versions,
                    sampling_settings={
                        "temperature": 0,
                        "max_output_tokens": 512,
                        "cache_mode": "bypass",
                        "latency_budget_ms": 30000,
                    },
                    model_revision_limitations=(
                        "Synthetic fixed mock; live model aliases and revisions not evaluated."
                    ),
                    status="queued",
                )
                session.add(run)
                await session.flush()
                for model_id in model_ids:
                    for case in manifest.cases:
                        session.add(
                            EvaluationCase(
                                run_id=run.id,
                                case_id=case.id,
                                case_sha256=case_hash(case),
                                model_id=model_id,
                                status="queued",
                            )
                        )
            return await self.get(principal, run.id)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def get(self, principal: Principal, run_id: UUID) -> EvaluationRunResponse:
        try:
            async with self.store.sessions() as session:
                run = await session.scalar(
                    select(EvaluationRun).where(
                        EvaluationRun.id == run_id,
                        EvaluationRun.tenant_id == principal.tenant_id,
                        EvaluationRun.application_id == principal.application_id,
                    )
                )
                if run is None:
                    raise GatewayError(
                        "FORBIDDEN", "Evaluation run is unavailable in this scope.", 403
                    )
                dataset = await session.get(EvaluationDataset, run.dataset_id)
                if dataset is None:
                    raise StateUnavailable()
                cases = (
                    await session.scalars(
                        select(EvaluationCase)
                        .where(EvaluationCase.run_id == run.id)
                        .order_by(EvaluationCase.case_id, EvaluationCase.model_id)
                    )
                ).all()
                report = None
                if run.status == "completed" and all(case.score is not None for case in cases):
                    report = aggregate([case.score for case in cases if case.score is not None])
                    durations = sorted(
                        case.latency_ms for case in cases if case.latency_ms is not None
                    )
                    known_costs = [
                        case.estimated_cost_usd
                        for case in cases
                        if case.estimated_cost_usd is not None
                    ]
                    report["measurements"] = {
                        "p50_latency_ms": (
                            str(durations[ceil(0.50 * len(durations)) - 1]) if durations else None
                        ),
                        "p95_latency_ms": (
                            str(durations[ceil(0.95 * len(durations)) - 1]) if durations else None
                        ),
                        "estimated_fresh_cost_usd": str(sum(known_costs, Decimal(0))),
                        "cost_evidence_complete": len(known_costs) == len(cases),
                        "latency_evidence_complete": len(durations) == len(cases),
                        "unknown_usage_attempts": sum(
                            int(case.score.get("unknown_usage_attempts", 0))
                            for case in cases
                            if case.score is not None
                        ),
                        "held_liability_usd": str(
                            sum(
                                (
                                    Decimal(str(case.score.get("held_liability_usd", "0")))
                                    for case in cases
                                    if case.score is not None
                                ),
                                Decimal(0),
                            )
                        ),
                    }
                return EvaluationRunResponse(
                    run_id=run.id,
                    status=run.status,
                    dataset_name=dataset.name,
                    dataset_version=dataset.version,
                    dataset_sha256=run.dataset_sha256,
                    model_ids=run.model_ids,
                    policy_version=run.policy_version,
                    threshold_profile=run.threshold_profile,
                    threshold_sha256=run.threshold_sha256,
                    code_revision=run.code_revision,
                    prompt_version=run.prompt_version,
                    evaluator_version=run.evaluator_version,
                    pricing_versions=run.pricing_versions,
                    sampling_settings=run.sampling_settings,
                    model_revision_limitations=run.model_revision_limitations,
                    error_code=run.error_code,
                    cases=[
                        EvaluationCaseResponse(
                            case_id=case.case_id,
                            model_id=case.model_id,
                            status=case.status,
                            request_id=case.request_id,
                            error_code=case.error_code,
                            score=case.score,
                            latency_ms=case.latency_ms,
                            estimated_cost_usd=case.estimated_cost_usd,
                        )
                        for case in cases
                    ],
                    report=report,
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def recover_interrupted(self) -> int:
        """At startup only, after acquiring the single-worker advisory lock."""
        try:
            async with self.store.sessions.begin() as session:
                rows = (
                    await session.scalars(
                        select(EvaluationCase)
                        .where(EvaluationCase.status == "running")
                        .with_for_update()
                    )
                ).all()
                for case in rows:
                    case.status = "uncertain"
                    case.error_code = "EXECUTION_UNCERTAIN"
                    case.completed_at = datetime.now(UTC)
                    run = await session.get(EvaluationRun, case.run_id)
                    if run is not None:
                        run.status = "interrupted"
                        run.error_code = "EXECUTION_UNCERTAIN"
                        run.completed_at = datetime.now(UTC)
                return len(rows)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def review_generation(
        self, principal: Principal, run_id: UUID, case_id: str, *, accepted: bool
    ) -> None:
        if principal.role != "operator":
            raise GatewayError("FORBIDDEN", "Operator review is required.", 403)
        try:
            async with self.store.sessions.begin() as session:
                run = await session.scalar(
                    select(EvaluationRun).where(
                        EvaluationRun.id == run_id,
                        EvaluationRun.tenant_id == principal.tenant_id,
                        EvaluationRun.application_id == principal.application_id,
                    )
                )
                if run is None or run.status != "completed":
                    raise GatewayError("FORBIDDEN", "Completed run is unavailable.", 403)
                case = await session.scalar(
                    select(EvaluationCase)
                    .where(EvaluationCase.run_id == run_id, EvaluationCase.case_id == case_id)
                    .with_for_update()
                )
                if (
                    case is None
                    or case.score is None
                    or case.score.get("task") != "generation"
                    or case.score.get("human_review_status") != "pending"
                ):
                    raise GatewayError(
                        "INVALID_REQUEST", "Pending generation review is unavailable.", 422
                    )
                case.score = {
                    **case.score,
                    "human_review_status": "accepted" if accepted else "rejected",
                    "human_review_credential_id": str(principal.credential_id),
                    "human_review_at": datetime.now(UTC).isoformat(),
                }
                session.add(
                    AuditEvent(
                        actor_id=principal.credential_id,
                        action="evaluation.review.accepted"
                        if accepted
                        else "evaluation.review.rejected",
                        target_id=str(case.id),
                    )
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def approve_baseline(self, principal: Principal, run_id: UUID) -> UUID:
        if principal.role != "operator":
            raise GatewayError("FORBIDDEN", "Operator baseline approval is required.", 403)
        report = await self.get(principal, run_id)
        measurements = cast(dict[str, Any], (report.report or {}).get("measurements", {}))
        generation = cast(dict[str, Any], (report.report or {}).get("generation", {}))
        if (
            report.status != "completed"
            or not measurements.get("cost_evidence_complete")
            or not measurements.get("latency_evidence_complete")
            or measurements.get("unknown_usage_attempts")
            or Decimal(str(measurements.get("held_liability_usd", "0"))) != 0
            or generation.get("human_review_pending", 0)
            or generation.get("human_review_accepted", 0) != generation.get("total_cases", 0)
        ):
            raise GatewayError("INVALID_REQUEST", "Baseline evidence is incomplete.", 422)
        try:
            async with self.store.sessions.begin() as session:
                run = await session.scalar(
                    select(EvaluationRun)
                    .where(
                        EvaluationRun.id == run_id, EvaluationRun.tenant_id == principal.tenant_id
                    )
                    .with_for_update()
                )
                if run is None or run.status != "completed":
                    raise GatewayError("FORBIDDEN", "Completed run is unavailable.", 403)
                baseline = EvaluationBaseline(
                    tenant_id=run.tenant_id,
                    application_id=run.application_id,
                    dataset_id=run.dataset_id,
                    profile_sha256=run.threshold_sha256,
                    run_id=run.id,
                    actor_id=principal.credential_id,
                )
                session.add(baseline)
                await session.flush()
                session.add(
                    AuditEvent(
                        actor_id=principal.credential_id,
                        action="evaluation.baseline_approved",
                        target_id=str(run.id),
                    )
                )
                return baseline.id
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def gate(self, principal: Principal, run_id: UUID) -> GateResult:
        current = await self.get(principal, run_id)
        if current.status != "completed":
            return GateResult(
                status="blocked",
                baseline_run_id=None,
                reasons=["run_not_completed"],
                deltas={},
            )
        try:
            profile = load_profile(current.threshold_profile)
        except (ValueError, OSError):
            return GateResult(
                status="blocked",
                baseline_run_id=None,
                reasons=["threshold_profile_unavailable"],
                deltas={},
            )
        if profile.content_sha256 != current.threshold_sha256:
            return GateResult(
                status="blocked",
                baseline_run_id=None,
                reasons=["threshold_profile_changed"],
                deltas={},
            )
        try:
            async with self.store.sessions() as session:
                run = await session.get(EvaluationRun, run_id)
                if run is None or run.tenant_id != principal.tenant_id:
                    raise GatewayError("FORBIDDEN", "Evaluation run is unavailable.", 403)
                baseline = await session.scalar(
                    select(EvaluationBaseline).where(
                        EvaluationBaseline.tenant_id == principal.tenant_id,
                        EvaluationBaseline.application_id == principal.application_id,
                        EvaluationBaseline.dataset_id == run.dataset_id,
                        EvaluationBaseline.profile_sha256 == run.threshold_sha256,
                    )
                )
                baseline_id = baseline.run_id if baseline else None
            approved = await self.get(principal, baseline_id) if baseline_id is not None else None
            return compare(
                current.report,
                approved.report if approved is not None else None,
                profile,
                baseline_id,
            )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def accounting_for_request(self, work: CaseWork) -> tuple[Decimal | None, int, Decimal]:
        """Include observed attempt costs and retained unknown-usage liability."""
        try:
            async with self.store.sessions() as session:
                request = await session.get(RequestRecord, work.request_id)
                if (
                    request is None
                    or request.evaluation_run_id != work.run_id
                    or request.traffic_kind != "evaluation"
                ):
                    return None, 0, Decimal(0)
                usage = (
                    await session.scalars(
                        select(UsageEvent).where(UsageEvent.request_id == work.request_id)
                    )
                ).all()
                held = (
                    await session.scalars(
                        select(SpendReservation).where(
                            SpendReservation.request_id == work.request_id,
                            SpendReservation.state == "held",
                        )
                    )
                ).all()
                return (
                    sum((row.estimated_cost_usd for row in usage), Decimal(0)),
                    sum(row.usage_status == "unknown" for row in usage),
                    sum((row.reserved_usd for row in held), Decimal(0)),
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def claim_case(self) -> CaseWork | None:
        try:
            async with self.store.sessions.begin() as session:
                case = await session.scalar(
                    select(EvaluationCase)
                    .join(EvaluationRun, EvaluationRun.id == EvaluationCase.run_id)
                    .where(
                        EvaluationRun.status.in_(["queued", "running"]),
                        EvaluationCase.status == "queued",
                    )
                    .order_by(EvaluationRun.created_at, EvaluationCase.case_id)
                    .with_for_update(of=EvaluationCase, skip_locked=True)
                    .limit(1)
                )
                if case is None:
                    return None
                run = await session.get(EvaluationRun, case.run_id)
                if run is None:
                    raise StateUnavailable()
                dataset = await session.get(EvaluationDataset, run.dataset_id)
                if dataset is None:
                    raise StateUnavailable()
                case.status = "running"
                case.request_id = uuid4()
                case.started_at = datetime.now(UTC)
                run.status = "running"
                run.started_at = run.started_at or datetime.now(UTC)
                return CaseWork(
                    case.id,
                    run.id,
                    case.request_id,
                    case.case_id,
                    case.model_id,
                    dataset.name,
                    dataset.version,
                    run.dataset_sha256,
                    case.case_sha256,
                    run.policy_version,
                    run.threshold_profile,
                    run.threshold_sha256,
                    run.code_revision,
                    run.tenant_id,
                    run.application_id,
                    run.credential_id,
                )
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error

    async def finish_case(
        self,
        work: CaseWork,
        *,
        status: str,
        error_code: str | None,
        score: dict[str, object],
        latency_ms: Decimal,
        estimated_cost_usd: Decimal | None,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("Invalid terminal case status")
        try:
            async with self.store.sessions.begin() as session:
                case = await session.scalar(
                    select(EvaluationCase).where(EvaluationCase.id == work.row_id).with_for_update()
                )
                if case is None or case.status != "running" or case.request_id != work.request_id:
                    raise StateUnavailable()
                case.status = status
                case.error_code = error_code
                case.score = score
                case.latency_ms = latency_ms
                case.estimated_cost_usd = estimated_cost_usd
                case.completed_at = datetime.now(UTC)
                remaining = await session.scalar(
                    select(EvaluationCase.id)
                    .where(
                        EvaluationCase.run_id == work.run_id,
                        EvaluationCase.status.in_(["queued", "running"]),
                        EvaluationCase.id != work.row_id,
                    )
                    .limit(1)
                )
                if remaining is None:
                    run = await session.get(EvaluationRun, work.run_id)
                    if run is not None:
                        run.status = "completed"
                        run.completed_at = datetime.now(UTC)
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise StateUnavailable() from error
