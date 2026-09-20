from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import APIKeyHeader
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.api.middleware import RequestBoundary
from app.api.schemas import (
    ErrorDetail,
    ErrorResponse,
    ExtractRequest,
    ExtractResponse,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
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
) -> FastAPI:
    settings = settings or load_settings()
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
        settings, store, provider, providers=providers, control=control, cache=cache
    )
    active_store = store

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()
        if control_redis is not None:
            await control_redis.aclose()
        if cache_redis is not None:
            await cache_redis.aclose()

    application = FastAPI(
        title="LLM Reliability Gateway",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Milestones 1–4: offline mock execution with versioned routing, bounded retries, "
            "shared Redis admission/circuits, optional encrypted keyed replay, atomic UTC "
            "budgets, and opt-in encrypted exact cache. Live adapters are fixture-tested but "
            "cannot dispatch through this API; evaluation execution and staging remain unavailable."
        ),
    )
    application.add_middleware(RequestBoundary, limit=settings.body_limit_bytes)

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
        response = await service.execute(
            payload, principal, request.state.request_id, request.state.started
        )
        assert isinstance(response, GenerateResponse)
        return response

    @application.post("/v1/extract", response_model=ExtractResponse, responses=ERROR_RESPONSES)
    async def extract(
        payload: ExtractRequest,
        request: Request,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> ExtractResponse:
        response = await service.execute(
            payload, principal, request.state.request_id, request.state.started
        )
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
            "telemetry": "not_configured",
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
