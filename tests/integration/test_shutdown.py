"""Probes and the preStop drain sequence."""

from __future__ import annotations

import asyncio

from tests.conftest import sse_events
from tests.fakes.upstream import chunks, wait


async def test_probes(gateway):
    assert (await gateway.client.get("/healthz")).json() == {"status": "ok"}
    assert (await gateway.client.get("/readyz")).status_code == 200


async def test_drain_flips_readyz(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(1), wait(0.5), chunks(2))
    body = {"model": "gpt", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    async with gateway.client.stream(
        "POST", "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}
    ) as r:
        lines = r.aiter_lines()
        first = await anext(lines)  # the stream is now in flight
        assert first.startswith("data: ")

        assert (await gateway.client.post("/internal/drain")).status_code == 200
        assert (await gateway.client.get("/readyz")).status_code == 503
        refused = await gateway.chat(key)
        assert refused.status_code == 503 and refused.headers["retry-after"] == "1"

        rest = [line async for line in lines]
    assert sse_events(rest)[-1] == "[DONE]"  # the in-flight stream completed


async def test_drain_is_loopback_only(gateway):
    # The test client connects over 127.0.0.1, so fake a remote caller at the ASGI level.
    app = gateway.server.server.config.app
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/internal/drain",
        "raw_path": b"/internal/drain",
        "query_string": b"",
        "headers": [],
        "client": ("10.1.2.3", 5555),
        "server": ("127.0.0.1", 80),
        "scheme": "http",
        "root_path": "",
    }
    await asyncio.wait_for(app(scope, receive, send), 2)
    assert sent[0]["status"] == 404
    assert gateway.deps.draining is False
