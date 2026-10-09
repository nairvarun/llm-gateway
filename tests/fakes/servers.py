"""Run ASGI apps on real sockets inside the test's event loop.

Integration tests need real servers: httpx's ASGITransport buffers response bodies, which would
hide exactly the streaming behavior under test.
"""

from __future__ import annotations

import asyncio
import socket

import uvicorn


class RunningServer:
    def __init__(self, server: uvicorn.Server, task: asyncio.Task, url: str) -> None:
        self.server, self.task, self.url = server, task, url

    async def stop(self) -> None:
        self.server.should_exit = True
        await self.task


async def start_server(app) -> RunningServer:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(
        app, lifespan="on", log_level="warning", http="h11", timeout_graceful_shutdown=2
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        if task.done():
            task.result()  # surface startup errors
        await asyncio.sleep(0.01)
    return RunningServer(server, task, f"http://127.0.0.1:{port}")
