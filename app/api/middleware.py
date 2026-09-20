import asyncio
from time import monotonic
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBoundary:
    """Bound buffered bodies even without Content-Length; generate trusted IDs."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app, self.limit = app, limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4()
        scope.setdefault("state", {}).update(request_id=request_id, started=monotonic())

        async def correlated(message: Message) -> None:
            if message["type"] == "http.response.start":
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
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
