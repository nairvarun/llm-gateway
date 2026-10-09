"""The FastAPI app: routes, the pipeline list, startup and shutdown.

Run with: uvicorn gateway.app:create_app_from_env --factory
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from gateway import metrics, tracing
from gateway.admin import bearer_from, make_admin_router
from gateway.config import Config, Settings, load_config
from gateway.context import GatewayResponse, RequestContext
from gateway.errors import BadRequest, GatewayError, Unavailable
from gateway.limiter.memory import MemoryLimiter
from gateway.providers.anthropic import AnthropicProvider
from gateway.providers.base import Provider
from gateway.providers.openai import OpenAIProvider
from gateway.resilience.breaker import Breaker
from gateway.schemas import ChatRequest
from gateway.sse import DONE, encode_event
from gateway.stages.auth import Auth, allowed, authenticate
from gateway.stages.base import Stage, build_chain
from gateway.stages.budget import Budget
from gateway.stages.cache import Cache
from gateway.stages.observe import Observe
from gateway.stages.rate_limit import RateLimit
from gateway.stages.route import Route
from gateway.stages.usage import UsageStage
from gateway.store.sqlite import SqliteStore
from gateway.streams import StreamError, StreamItem, drain_background

log = logging.getLogger("gateway")

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
PROVIDER_CLASSES = {"openai": OpenAIProvider, "anthropic": AnthropicProvider}


@dataclass
class Deps:
    cfg: Config
    settings: Settings
    store: SqliteStore
    providers: dict[str, Provider]
    breakers: dict[str, Breaker]
    limiter: MemoryLimiter = field(default_factory=MemoryLimiter)
    draining: bool = False


def request_id_from(request: Request) -> str:
    incoming = request.headers.get("x-request-id", "")
    return incoming if REQUEST_ID_RE.match(incoming) else "req_" + secrets.token_hex(12)


class SSEResponse(StreamingResponse):
    """A streaming response that always watches for client disconnects.

    When the client goes away, the task iterating the body is cancelled, and that cancellation
    runs every stage's cleanup. Starlette only does this for older ASGI spec versions, so the
    gateway does it itself rather than depend on the server's version.
    """

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        async with anyio.create_task_group() as tg:

            async def stream() -> None:
                try:
                    await self.stream_response(send)
                except OSError:
                    pass  # client already gone
                tg.cancel_scope.cancel()

            tg.start_soon(stream)
            await self.listen_for_disconnect(receive)
            tg.cancel_scope.cancel()


async def sse_bytes(stream: AsyncIterator[StreamItem]) -> AsyncIterator[bytes]:
    errored = False
    async for item in stream:
        if isinstance(item, StreamError):
            errored = True  # the stream ends right after this item
            yield encode_event(item.error.body())
        else:
            yield encode_event(item.model_dump(exclude_none=True))
    if not errored:
        yield DONE


def to_http(resp: GatewayResponse, ctx: RequestContext) -> Response:
    headers = {"x-request-id": ctx.request_id, **resp.headers}
    if resp.stream is not None:
        headers |= {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return SSEResponse(sse_bytes(resp.stream), media_type="text/event-stream", headers=headers)
    return JSONResponse(resp.body, status_code=resp.status, headers=headers)


def build_providers(
    cfg: Config, environ: Mapping[str, str], transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Provider]:
    return {
        name: PROVIDER_CLASSES[name](p, environ[p.api_key_env], cfg.timeouts.connect_s, transport)
        for name, p in cfg.providers.items()
    }


def create_app(
    cfg: Config,
    settings: Settings,
    environ: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    environ = os.environ if environ is None else environ
    providers = build_providers(cfg, environ, transport)
    breakers = {
        name: Breaker(name, cfg.breaker.failure_threshold, cfg.breaker.open_s) for name in providers
    }
    deps = Deps(cfg, settings, SqliteStore(settings.gateway_db_path), providers, breakers)

    stages: list[Stage] = [
        Observe(),
        Auth(deps.store),
        UsageStage(deps.store, cfg.pricing),
        RateLimit(deps.limiter),
        *([Budget(deps.store)] if cfg.stages.budget.enabled else []),
        *([Cache(cfg.stages.cache)] if cfg.stages.cache.enabled else []),
    ]
    chain = build_chain(stages, terminal=Route(cfg, providers, breakers))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await deps.store.open()
        tracing.setup(settings.otel_exporter_otlp_endpoint)
        yield
        await drain_background(5)
        for p in providers.values():
            await p.aclose()
        await deps.store.close()

    app = FastAPI(title="LLM Gateway", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.deps = deps

    @app.exception_handler(GatewayError)
    async def gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        headers = exc.headers()
        rid = getattr(request.state, "request_id", None)
        if rid:
            headers["x-request-id"] = rid
        return JSONResponse(exc.body(), status_code=exc.status, headers=headers)

    def reject(reason: str, exc: GatewayError) -> GatewayError:
        metrics.rejected.labels(reason).inc()
        return exc

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Response:
        request.state.request_id = request_id_from(request)
        if deps.draining:
            raise reject("draining", Unavailable("the gateway is restarting", retry_after=1))
        try:
            body = ChatRequest.model_validate(await request.json())
        except (ValueError, ValidationError) as e:
            raise reject("bad_request", BadRequest(f"invalid request body: {e}")) from e
        if body.model not in cfg.models:
            raise reject(
                "unknown_model",
                BadRequest(f"unknown model {body.model!r}", code="model_not_found"),
            )
        ctx = RequestContext(
            request_id=request.state.request_id,
            body=body,
            alias=body.model,
            bearer=bearer_from(request),
            cache_mode="bypass"
            if request.headers.get("x-gateway-cache", "").lower() == "bypass"
            else "default",
        )
        resp = await chain(ctx)
        return to_http(resp, ctx)

    @app.get("/v1/models")
    async def list_models(request: Request) -> dict:
        key = await authenticate(deps.store, bearer_from(request))
        data = [
            {"id": alias, "object": "model", "created": 0, "owned_by": "llm-gateway"}
            for alias in cfg.models
            if allowed(key, alias)
        ]
        return {"object": "list", "data": data}

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        if deps.draining:
            return JSONResponse({"status": "draining"}, status_code=503)
        try:
            async with asyncio.timeout(1):
                ok = await deps.store.ping()
        except Exception:
            ok = False
        if not ok:
            return JSONResponse({"status": "store unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/internal/drain")
    async def drain(request: Request) -> Response:
        # Called by the preStop hook over loopback; invisible to anyone else.
        if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        deps.draining = True
        log.info('{"event": "draining"}')
        await asyncio.sleep(cfg.shutdown.drain_delay_s)
        return JSONResponse({"status": "draining"})

    app.include_router(make_admin_router(deps))
    return app


def create_app_from_env() -> FastAPI:
    settings = Settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(message)s")
    cfg = load_config(settings.gateway_config)
    return create_app(cfg, settings)
