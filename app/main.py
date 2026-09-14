from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
)
from app.config import Settings, load_settings
from app.domain.errors import GatewayError
from app.domain.models import Principal, Provider, StateUnavailable, Store
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
    service = GatewayService(settings, store, provider)
    active_store, active_settings = store, settings

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()

    application = FastAPI(
        title="LLM Reliability Gateway",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Milestone 1: single-attempt offline mock generation/extraction. "
            "No live providers, cache, keyed replay, or evaluation execution yet."
        ),
    )
    application.add_middleware(RequestBoundary, limit=settings.body_limit_bytes)

    def error_response(request: Request, error: GatewayError) -> JSONResponse:
        envelope = ErrorResponse(
            request_id=request.state.request_id,
            error=ErrorDetail(code=error.code, message=error.message, retryable=error.retryable),
        )
        return JSONResponse(envelope.model_dump(mode="json"), status_code=error.status)

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
        if active_settings.redis_url is not None:
            redis = Redis.from_url(
                active_settings.redis_url.get_secret_value(),
                socket_timeout=1,
                socket_connect_timeout=1,
            )
            try:
                await redis.ping()
                components["redis"] = "healthy_optional"
            except (OSError, TimeoutError, RedisError):
                components["redis"] = "degraded_optional"
            finally:
                await redis.aclose()
        response = HealthResponse(status="ready" if healthy else "not_ready", components=components)
        return JSONResponse(response.model_dump(mode="json"), status_code=200 if healthy else 503)

    return application
