"""Timeouts, retries, fallback and the breaker, end to end."""

from __future__ import annotations

from tests.conftest import sse_events
from tests.fakes.upstream import chunks, drop, status, wait


async def stream_events(gateway, key, **body) -> tuple[int, list]:
    body.setdefault("model", "gpt")
    body.setdefault("messages", [{"role": "user", "content": "hello"}])
    body["stream"] = True
    async with gateway.client.stream(
        "POST", "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}
    ) as r:
        if r.status_code != 200:
            await r.aread()
            return r.status_code, [r.json()]
        return 200, sse_events([line async for line in r.aiter_lines()])


async def test_retry_then_fallback(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(status(503))  # primary (openai) fails
    upstream.script(chunks(2))  # fallback (anthropic) succeeds
    code, events = await stream_events(gateway, key, model="fast")
    assert code == 200 and events[-1] == "[DONE]"
    assert [r["path"] for r in upstream.requests] == ["/v1/chat/completions", "/v1/messages"]
    row = (await gateway.wait_for_rows(1))[0]
    assert (row["attempts"], row["provider"], row["status"]) == (2, "anthropic", 200)


async def test_single_target_retries_same_provider(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(status(500))
    upstream.script(chunks(1))
    r = await gateway.chat(key, model="gpt")
    assert r.status_code == 200
    row = (await gateway.wait_for_rows(1))[0]
    assert (row["attempts"], row["provider"]) == (2, "openai")


async def test_retries_exhausted_returns_502(gateway, upstream):
    key = await gateway.create_key()
    for _ in range(3):
        upstream.script(status(500))
    r = await gateway.chat(key, model="gpt")
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "api_error"
    assert len(upstream.requests) == 3
    row = (await gateway.wait_for_rows(1))[0]
    assert (row["status"], row["attempts"], row["cost_usd"]) == (502, 3, 0)


async def test_client_error_is_not_retried(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(status(400))
    r = await gateway.chat(key, model="gpt")
    assert r.status_code == 400
    assert len(upstream.requests) == 1


async def test_first_byte_timeout_retries_then_504(start_gateway, upstream):
    gw = await start_gateway(timeouts={"first_byte_s": 0.3}, retries={"max_attempts": 2})
    key = await gw.create_key()
    upstream.script(wait(2), chunks(1))
    upstream.script(wait(2), chunks(1))
    code, body = await stream_events(gw, key, model="gpt")
    assert code == 504
    assert body[0]["error"]["type"] == "timeout_error"
    assert len(upstream.requests) == 2


async def test_no_retry_after_first_byte(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(1), drop())
    code, events = await stream_events(gateway, key, model="gpt")
    assert code == 200
    assert "error" in events[-1] and "[DONE]" not in events
    assert len(upstream.requests) == 1
    row = (await gateway.wait_for_rows(1))[0]
    assert row["status"] == 502


async def test_idle_timeout_sends_sse_error(start_gateway, upstream):
    gw = await start_gateway(timeouts={"idle_s": 0.5})
    key = await gw.create_key()
    upstream.script(chunks(1), wait(3), chunks(1))
    code, events = await stream_events(gw, key, model="gpt")
    assert code == 200
    assert events[-1]["error"]["type"] == "timeout_error"
    assert "[DONE]" not in events
    row = (await gw.wait_for_rows(1))[0]
    assert (row["status"], row["usage_estimated"]) == (504, 1)


async def test_breaker_opens_and_returns_503(start_gateway, upstream):
    gw = await start_gateway(
        breaker={"failure_threshold": 2, "open_s": 60}, retries={"max_attempts": 1}
    )
    key = await gw.create_key()
    upstream.script(status(500))
    upstream.script(status(500))
    assert (await gw.chat(key, model="gpt")).status_code == 502
    assert (await gw.chat(key, model="gpt")).status_code == 502
    r = await gw.chat(key, model="gpt")  # breaker is open: no upstream call
    assert r.status_code == 503
    assert r.headers["retry-after"] == "1"
    assert len(upstream.requests) == 2


async def test_open_breaker_falls_back(start_gateway, upstream):
    gw = await start_gateway(
        breaker={"failure_threshold": 1, "open_s": 60}, retries={"max_attempts": 1}
    )
    key = await gw.create_key()
    upstream.script(status(500))
    assert (await gw.chat(key, model="gpt")).status_code == 502  # opens the openai breaker
    r = await gw.chat(key, model="fast")  # openai skipped, anthropic used
    assert r.status_code == 200
    assert upstream.requests[-1]["path"] == "/v1/messages"
