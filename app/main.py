from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import APIKeyHeader
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.responses import Response as StarletteResponse

from app.api.middleware import RequestBoundary
from app.api.schemas import (
    ErrorDetail,
    ErrorResponse,
    EvaluationRunRequest,
    EvaluationRunResponse,
    ExtractRequest,
    ExtractResponse,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    MetricsSummaryResponse,
    SpendBucketResponse,
    SpendSummaryResponse,
)
from app.cache.redis_cache import Cache, RedisCache
from app.config import Settings, load_settings
from app.domain.control import (
    Control,
    ControlLimits,
    LocalControl,
    RedisControl,
    UnavailableControl,
)
from app.domain.deadline import DeadlineExpired
from app.domain.errors import GatewayError
from app.domain.models import Principal, Provider, SpendBucketView, StateUnavailable, Store
from app.evaluation.datasets import load_dataset
from app.evaluation.gates import GateResult
from app.evaluation.profiles import load_profile
from app.evaluation.repository import EvaluationRepository
from app.evaluation.revision import code_revision
from app.observability.summary import bounded_window, summary
from app.observability.telemetry import Telemetry
from app.persistence.database import database_engine
from app.persistence.store import PostgresStore
from app.providers.mock import MockProvider, MockStep
from app.security.auth import hash_api_key
from app.service import GatewayService

key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (401, 403, 409, 413, 422, 429, 502, 503, 504)
}


def create_app(
    settings: Settings | None = None,
    *,
    store: Store | None = None,
    provider: Provider | None = None,
    providers: Mapping[tuple[str, str], Provider] | None = None,
    control: Control | None = None,
    cache: Cache | None = None,
    telemetry: Telemetry | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    telemetry = telemetry or Telemetry(settings)
    engine = (
        database_engine(settings.database_url.get_secret_value(), settings.database_schema)
        if store is None
        else None
    )
    if store is None:
        assert engine is not None
        store = PostgresStore(engine)
    provider = provider or MockProvider([MockStep(settings.mock_scenario)])
    control_redis = (
        Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_timeout=1,
            socket_connect_timeout=1,
        )
        if settings.redis_url is not None
        else None
    )
    cache_url = settings.cache_redis_url or settings.redis_url
    cache_redis = (
        Redis.from_url(cache_url.get_secret_value(), socket_timeout=1, socket_connect_timeout=1)
        if cache is None and settings.cache_encryption_key is not None and cache_url is not None
        else None
    )
    if cache is None and cache_redis is not None:
        cache = RedisCache(cache_redis)
    if control is None:
        if control_redis is not None:
            control = RedisControl(
                control_redis,
                ControlLimits(
                    tenant_concurrency=settings.tenant_concurrency,
                    provider_concurrency=settings.provider_concurrency,
                    tenant_rate_per_minute=settings.tenant_rate_per_minute,
                    provider_rate_per_minute=settings.provider_rate_per_minute,
                    failure_threshold=settings.circuit_failure_threshold,
                    failure_window_seconds=settings.circuit_window_seconds,
                    open_cooldown_seconds=settings.circuit_cooldown_seconds,
                    probe_lease_seconds=settings.circuit_probe_lease_seconds,
                ),
            )
        else:
            control = LocalControl() if engine is None else UnavailableControl()
    service = GatewayService(
        settings,
        store,
        provider,
        providers=providers,
        control=control,
        cache=cache,
        telemetry=telemetry,
    )
    active_store = store
    evaluation = EvaluationRepository(store) if isinstance(store, PostgresStore) else None

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()
        if control_redis is not None:
            await control_redis.aclose()
        if cache_redis is not None:
            await cache_redis.aclose()
        telemetry.close()

    application = FastAPI(
        title="LLM Reliability Gateway",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Milestones 1–5: offline mock execution with versioned routing, bounded retries, "
            "shared Redis admission/circuits, optional encrypted keyed replay, atomic UTC "
            "budgets, opt-in encrypted exact cache, synthetic evaluation, and tenant-scoped "
            "summaries. Live adapters are fixture-tested but cannot dispatch through this API; "
            "authorized cloud staging remains unavailable."
        ),
    )
    application.add_middleware(
        RequestBoundary, limit=settings.body_limit_bytes, telemetry=telemetry
    )
    application.state.gateway_service = service
    application.state.telemetry = telemetry

    def error_response(request: Request, error: GatewayError) -> JSONResponse:
        envelope = ErrorResponse(
            request_id=request.state.request_id,
            original_request_id=error.original_request_id,
            error=ErrorDetail(code=error.code, message=error.message, retryable=error.retryable),
        )
        headers = (
            {"Retry-After": str(error.retry_after_seconds)}
            if error.retry_after_seconds is not None
            else None
        )
        return JSONResponse(
            envelope.model_dump(mode="json"), status_code=error.status, headers=headers
        )

    @application.exception_handler(GatewayError)
    async def gateway_error(request: Request, error: GatewayError) -> JSONResponse:
        return error_response(request, error)

    @application.exception_handler(StateUnavailable)
    async def state_error(request: Request, error: StateUnavailable) -> JSONResponse:
        return error_response(
            request,
            GatewayError(
                "DEPENDENCY_UNAVAILABLE",
                "Critical state is unavailable; check database migrations/configuration.",
                503,
                retryable=True,
            ),
        )

    @application.exception_handler(DeadlineExpired)
    async def deadline_error(request: Request, error: DeadlineExpired) -> JSONResponse:
        return error_response(
            request, GatewayError("DEADLINE_EXCEEDED", "Request deadline expired.", 504)
        )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return error_response(
            request, GatewayError("INVALID_REQUEST", "Request fields or JSON are invalid.", 422)
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return error_response(
            request,
            GatewayError("INVALID_REQUEST", "Route or method is unavailable.", error.status_code),
        )

    async def authenticate(key: Annotated[str | None, Depends(key_header)]) -> Principal:
        if key is None or not 1 <= len(key) <= 256:
            raise GatewayError("UNAUTHENTICATED", "A valid client API key is required.", 401)
        principal = await active_store.authenticate(hash_api_key(key))
        if principal is None:
            raise GatewayError("UNAUTHENTICATED", "A valid client API key is required.", 401)
        return principal

    @application.post("/v1/generate", response_model=GenerateResponse, responses=ERROR_RESPONSES)
    async def generate(
        payload: GenerateRequest,
        request: Request,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> GenerateResponse:
        with telemetry.tracer.start_as_current_span("gateway.execute") as span:
            response = await service.execute(
                payload, principal, request.state.request_id, request.state.started
            )
            span.set_attribute("gateway.cache_outcome", response.cache_status)
        telemetry.observe_cache(response.cache_status)
        assert isinstance(response, GenerateResponse)
        return response

    @application.post("/v1/extract", response_model=ExtractResponse, responses=ERROR_RESPONSES)
    async def extract(
        payload: ExtractRequest,
        request: Request,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> ExtractResponse:
        with telemetry.tracer.start_as_current_span("gateway.execute") as span:
            response = await service.execute(
                payload, principal, request.state.request_id, request.state.started
            )
            span.set_attribute("gateway.cache_outcome", response.cache_status)
        telemetry.observe_cache(response.cache_status)
        assert isinstance(response, ExtractResponse)
        return response

    @application.get("/v1/spend", response_model=SpendSummaryResponse, responses=ERROR_RESPONSES)
    async def spend(principal: Annotated[Principal, Depends(authenticate)]) -> SpendSummaryResponse:
        summary = await active_store.spend_summary(principal, principal.tenant_id)

        def view(bucket: SpendBucketView) -> SpendBucketResponse:
            return SpendBucketResponse(
                starts_at=bucket.starts_at,
                limit_usd=bucket.limit_usd,
                committed_usd=bucket.committed_usd,
                held_usd=bucket.held_usd,
                remaining_usd=max(
                    bucket.limit_usd - bucket.committed_usd - bucket.held_usd,
                    Decimal("0"),
                ),
            )

        return SpendSummaryResponse(
            tenant_id=summary.tenant_id, day=view(summary.day), month=view(summary.month)
        )

    @application.post(
        "/v1/evaluations/runs",
        response_model=EvaluationRunResponse,
        status_code=202,
        responses=ERROR_RESPONSES,
    )
    async def enqueue_evaluation(
        payload: EvaluationRunRequest,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> EvaluationRunResponse:
        if evaluation is None:
            raise GatewayError("DEPENDENCY_UNAVAILABLE", "Evaluation state is unavailable.", 503)
        try:
            manifest = load_dataset(payload.dataset_name, payload.dataset_version)
            profile = load_profile(payload.threshold_profile)
        except (ValueError, OSError) as error:
            raise GatewayError(
                "INVALID_REQUEST", "Approved evaluation input is unavailable.", 422
            ) from error
        if (profile.dataset_name, profile.dataset_version) != (manifest.name, manifest.version):
            raise GatewayError("INVALID_REQUEST", "Threshold profile does not match dataset.", 422)
        registry = await active_store.registry()
        if payload.policy_version != f"{registry.policy_name}@{registry.policy_version}":
            raise GatewayError("INVALID_REQUEST", "Pinned policy is unavailable.", 422)
        eligible = {
            f"{model.payload.model}@{model.version}"
            for model in registry.models
            if model.payload.provider == "mock"
            and model.payload.provider not in registry.disabled_providers
        }
        if (
            len(set(payload.model_ids)) != len(payload.model_ids)
            or not set(payload.model_ids) <= eligible
        ):
            raise GatewayError(
                "INVALID_REQUEST", "Only pinned offline mock models are eligible.", 422
            )
        return await evaluation.enqueue(
            principal,
            manifest,
            payload.model_ids,
            registry.policy_id,
            payload.policy_version,
            profile,
            code_revision(),
            {
                f"{model.payload.model}@{model.version}": model.pricing_version
                for model in registry.models
                if f"{model.payload.model}@{model.version}" in payload.model_ids
            },
        )

    @application.get(
        "/v1/evaluations/runs/{run_id}",
        response_model=EvaluationRunResponse,
        responses=ERROR_RESPONSES,
    )
    async def evaluation_run(
        run_id: UUID,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> EvaluationRunResponse:
        if evaluation is None:
            raise GatewayError("DEPENDENCY_UNAVAILABLE", "Evaluation state is unavailable.", 503)
        return await evaluation.get(principal, run_id)

    @application.get(
        "/v1/evaluations/runs/{run_id}/gate",
        response_model=GateResult,
        responses=ERROR_RESPONSES,
    )
    async def evaluation_gate(
        run_id: UUID,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> GateResult:
        if evaluation is None:
            raise GatewayError("DEPENDENCY_UNAVAILABLE", "Evaluation state is unavailable.", 503)
        return await evaluation.gate(principal, run_id)

    @application.get(
        "/v1/metrics/summary",
        response_model=MetricsSummaryResponse,
        responses=ERROR_RESPONSES,
    )
    async def metrics_summary(
        principal: Annotated[Principal, Depends(authenticate)],
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        traffic_kind: Literal["application", "evaluation"] = "application",
    ) -> MetricsSummaryResponse:
        if not isinstance(active_store, PostgresStore):
            raise GatewayError("DEPENDENCY_UNAVAILABLE", "Summary state is unavailable.", 503)
        start, end = bounded_window(starts_at, ends_at)
        return MetricsSummaryResponse.model_validate(
            await summary(active_store, principal, start, end, traffic_kind)
        )

    @application.get("/metrics", include_in_schema=False)
    async def metrics(principal: Annotated[Principal, Depends(authenticate)]) -> StarletteResponse:
        if principal.role != "operator":
            raise GatewayError("FORBIDDEN", "Operator metrics access is required.", 403)
        content, content_type = telemetry.render()
        return StarletteResponse(content, media_type=content_type)

    @application.get("/health/live", response_model=HealthResponse)
    async def live() -> HealthResponse:
        return HealthResponse(status="live")

    @application.get(
        "/health/ready", response_model=HealthResponse, responses={503: {"model": HealthResponse}}
    )
    async def ready() -> JSONResponse:
        healthy = await active_store.ready()
        components = {
            "database": "healthy" if healthy else "unavailable",
            "provider": "mock",
            "telemetry": telemetry.health,
            "cache": "not_configured",
        }
        if cache_redis is not None:
            try:
                await cache_redis.ping()
                components["cache"] = "healthy"
            except (OSError, RedisError):
                components["cache"] = "degraded"
        control_healthy = False
        if control_redis is not None:
            try:
                await control_redis.ping()
                control_healthy = True
                components["redis"] = "healthy"
                if isinstance(control, RedisControl):
                    circuit = await control.snapshot("mock")
                    components["circuit.mock"] = circuit.state
                    components["circuit.mock.transient_failures"] = str(circuit.transient_failures)
            except (OSError, TimeoutError, RedisError, StateUnavailable):
                components["redis"] = "unavailable_critical"
        else:
            components["redis"] = "local_test_only" if engine is None else "unconfigured_critical"
            control_healthy = engine is None
        ready_now = healthy and control_healthy
        response = HealthResponse(
            status="ready" if ready_now else "not_ready", components=components
        )
        return JSONResponse(response.model_dump(mode="json"), status_code=200 if ready_now else 503)

    return application
