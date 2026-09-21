from decimal import Decimal

from sqlalchemy import text

from app.api.schemas import ExtractRequest, GenerateRequest
from app.domain.errors import GatewayError
from app.domain.models import Principal
from app.evaluation.datasets import case_hash, load_dataset
from app.evaluation.profiles import load_profile
from app.evaluation.repository import CaseWork, EvaluationRepository
from app.evaluation.revision import code_revision
from app.evaluation.scoring import score_case
from app.persistence.store import PostgresStore
from app.service import GatewayService

WORKER_LOCK_ID = 843695120


async def execute_case(
    repository: EvaluationRepository, service: GatewayService, work: CaseWork
) -> None:
    manifest = load_dataset(work.dataset_name, work.dataset_version)
    profile = load_profile(work.threshold_profile)
    if (
        manifest.content_sha256 != work.dataset_sha256
        or profile.content_sha256 != work.threshold_sha256
        or code_revision() != work.code_revision
    ):
        raise ValueError("Pinned evaluation evidence changed; case remains uncertain")
    case = next((item for item in manifest.cases if item.id == work.case_id), None)
    if case is None or case_hash(case) != work.case_sha256:
        raise ValueError("Pinned case evidence changed; case remains uncertain")
    registry = await service.store.registry()
    if f"{registry.policy_name}@{registry.policy_version}" != work.policy_version or not any(
        f"{model.payload.model}@{model.version}" == work.model_id
        and model.payload.provider == "mock"
        for model in registry.models
    ):
        raise ValueError("Pinned routing configuration changed; case remains uncertain")
    principal = Principal(
        work.tenant_id,
        work.application_id,
        work.credential_id,
        "tenant",
        "evaluation",
        work.run_id,
    )
    request: GenerateRequest
    if case.task == "extraction":
        assert case.schema_ is not None
        request = ExtractRequest(input=case.resolved_input, json_schema=case.schema_)
    else:
        request = GenerateRequest(input=case.resolved_input, task_type=case.task)
    started = service.clock.now()
    output: object | None = None
    error_code: str | None = None
    estimated_cost: Decimal | None = None
    status = "completed"
    try:
        response = await service.execute(request, principal, work.request_id, started)
        output = response.output
        estimated_cost = response.estimated_cost_usd
    except GatewayError as error:
        error_code = error.code
        status = "failed"
    accounting_cost, unknown_count, held_liability = await repository.accounting_for_request(work)
    if accounting_cost is not None:
        estimated_cost = accounting_cost
    # Other exceptions, including a critical-state failure or cancellation, leave
    # the claim unresolved. A restarted worker will mark it uncertain.
    latency_ms = Decimal(str((service.clock.now() - started) * 1000))
    await repository.finish_case(
        work,
        status=status,
        error_code=error_code,
        score={
            **score_case(case, output, error_code),
            "unknown_usage_attempts": unknown_count,
            "held_liability_usd": str(held_liability),
        },
        latency_ms=latency_ms,
        estimated_cost_usd=estimated_cost,
    )


async def run_worker(store: PostgresStore, service: GatewayService, *, max_cases: int = 100) -> int:
    if not 1 <= max_cases <= 1000:
        raise ValueError("Worker case limit must be between 1 and 1000")
    repository = EvaluationRepository(store)
    async with store.engine.connect() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock)"), {"lock": WORKER_LOCK_ID}
        )
        if not acquired:
            raise RuntimeError("Another evaluation worker owns the single-worker lease")
        try:
            await repository.recover_interrupted()
            completed = 0
            while completed < max_cases:
                work = await repository.claim_case()
                if work is None:
                    break
                await execute_case(repository, service, work)
                completed += 1
            return completed
        finally:
            await connection.scalar(
                text("SELECT pg_advisory_unlock(:lock)"), {"lock": WORKER_LOCK_ID}
            )
