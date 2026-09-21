import asyncio
from time import monotonic
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.telemetry import Telemetry, route_label


class RequestBoundary:
    """Bound buffered bodies even without Content-Length; generate trusted IDs."""

    def __init__(self, app: ASGIApp, limit: int, telemetry: Telemetry | None = None) -> None:
        self.app, self.limit, self.telemetry = app, limit, telemetry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4()
        started = monotonic()
        scope.setdefault("state", {}).update(request_id=request_id, started=started)
        status_code = 500

        async def correlated(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append(
                    (b"x-request-id", str(request_id).encode())
                )
            await send(message)

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.limit:
                await JSONResponse(
                    status_code=413,
                    content={
                        "request_id": str(request_id),
                        "error": {
                            "code": "PAYLOAD_TOO_LARGE",
                            "message": "Request body exceeds the configured limit.",
                            "retryable": False,
                        },
                    },
                )(scope, receive, correlated)
                if self.telemetry is not None:
                    self.telemetry.observe_http(
                        scope.get("path", ""), status_code, monotonic() - started, request_id
                    )
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False
        disconnected = asyncio.Event()

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def execute_app() -> None:
            if self.telemetry is None:
                await self.app(scope, replay, correlated)
                return
            with self.telemetry.tracer.start_as_current_span("gateway.http") as span:
                span.set_attribute("gateway.request_id", str(request_id))
                span.set_attribute("gateway.route", route_label(scope.get("path", "")))
                await self.app(scope, replay, correlated)

        execution = asyncio.create_task(execute_app())

        async def watch_disconnect() -> None:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected.set()
                    execution.cancel()
                    return

        watcher = asyncio.create_task(watch_disconnect())
        try:
            done, _ = await asyncio.wait({execution, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher
                try:
                    await execution
                except asyncio.CancelledError:
                    pass
            else:
                await execution
        finally:
            if self.telemetry is not None:
                self.telemetry.observe_http(
                    scope.get("path", ""), status_code, monotonic() - started, request_id
                )
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
