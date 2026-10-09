"""Streaming through the gateway: no buffering, disconnects propagate, usage survives."""

from __future__ import annotations

import asyncio
import time

from tests.conftest import sse_events
from tests.fakes.upstream import chunks, usage, wait


async def stream_lines(gateway, key, **body) -> list[str]:
    body.setdefault("model", "gpt")
    body.setdefault("messages", [{"role": "user", "content": "hello"}])
    body["stream"] = True
    headers = {"Authorization": f"Bearer {key}"}
    async with gateway.client.stream(
        "POST", "/v1/chat/completions", json=body, headers=headers
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        return [line async for line in r.aiter_lines()]


async def test_openai_stream_passes_through(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(3), usage(7, 3))
    events = sse_events(await stream_lines(gateway, key))
    texts = [e["choices"][0]["delta"]["content"] for e in events if isinstance(e, dict)]
    assert texts == ["tok0 ", "tok1 ", "tok2 "]
    assert events[-1] == "[DONE]"
    assert all(e["model"] == "gpt" for e in events if isinstance(e, dict))  # alias, not model ID


async def test_anthropic_stream_is_translated(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(2), usage(5, 2))
    events = sse_events(await stream_lines(gateway, key, model="claude"))
    assert events[0]["choices"][0]["delta"]["role"] == "assistant"
    content = "".join(
        e["choices"][0]["delta"].get("content", "") for e in events if isinstance(e, dict)
    )
    assert content == "tok0 tok1 "
    finishes = [e["choices"][0]["finish_reason"] for e in events if isinstance(e, dict)]
    assert finishes[-1] == "stop"
    assert events[-1] == "[DONE]"
    assert upstream.requests[0]["path"] == "/v1/messages"
    assert upstream.requests[0]["headers"]["x-api-key"] == "ak-test"
    rows = await gateway.wait_for_rows(1)
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (5, 2)
    assert rows[0]["usage_estimated"] == 0


async def test_include_usage_injected_and_stripped(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(2), usage(11, 2))
    events = sse_events(await stream_lines(gateway, key))
    assert upstream.requests[0]["body"]["stream_options"] == {"include_usage": True}
    assert all(e["choices"] for e in events if isinstance(e, dict))  # no usage-only chunk
    rows = await gateway.wait_for_rows(1)
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (11, 2)


async def test_usage_chunk_kept_when_client_asks(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(1), usage(4, 1))
    events = sse_events(await stream_lines(gateway, key, stream_options={"include_usage": True}))
    usage_chunks = [e for e in events if isinstance(e, dict) and not e["choices"]]
    assert usage_chunks[0]["usage"]["prompt_tokens"] == 4


async def test_first_chunk_before_upstream_finishes(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(1), wait(2), chunks(5))
    body = {"model": "gpt", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    start = time.monotonic()
    async with gateway.client.stream(
        "POST", "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}
    ) as r:
        async for line in r.aiter_lines():
            if line.startswith("data: "):
                break
        assert time.monotonic() - start < 1.0


async def test_disconnect_closes_upstream(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(1), wait(10), chunks(1))
    body = {"model": "gpt", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    async with gateway.client.stream(
        "POST", "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}
    ) as r:
        async for line in r.aiter_lines():
            if line.startswith("data: "):
                break
    async with asyncio.timeout(2):
        while upstream.disconnects < 1:
            await asyncio.sleep(0.02)


async def test_disconnect_writes_estimated_usage(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(2), wait(10), chunks(1))
    body = {"model": "gpt", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    async with gateway.client.stream(
        "POST", "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}
    ) as r:
        seen = 0
        async for line in r.aiter_lines():
            if line.startswith("data: "):
                seen += 1
                if seen == 2:
                    break
    rows = await gateway.wait_for_rows(1)
    row = rows[0]
    assert row["status"] == 499
    assert row["usage_estimated"] == 1
    assert row["output_tokens"] == len("tok0 tok1 ") // 4
    assert row["cost_usd"] > 0
