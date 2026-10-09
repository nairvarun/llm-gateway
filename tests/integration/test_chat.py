"""Non-streaming requests, auth, limits, budgets, cache and /v1/models through the full app."""

from __future__ import annotations

import asyncio

from tests.fakes.upstream import chunks, usage, wait


async def test_non_streaming_openai(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(2), usage(12, 2))
    r = await gateway.chat(key, model="gpt")
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "gpt"
    assert body["choices"][0]["message"]["content"] == "tok0 tok1 "
    assert r.headers["x-request-id"].startswith("req_")
    assert "stream_options" not in upstream.requests[0]["body"]
    row = (await gateway.wait_for_rows(1))[0]
    # gpt-test costs $1 / $2 per 1M tokens
    assert row["cost_usd"] == round((12 * 1 + 2 * 2) / 1_000_000, 6)


async def test_non_streaming_anthropic(gateway, upstream):
    key = await gateway.create_key()
    upstream.script(chunks(3), usage(8, 3))
    r = await gateway.chat(
        key,
        model="claude",
        messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"},
        ],
        temperature=1.7,
        stop="END",
    )
    assert r.status_code == 200
    assert r.json()["usage"] == {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}
    sent = upstream.requests[0]["body"]
    assert sent["system"] == "be brief"
    assert sent["messages"] == [{"role": "user", "content": "hello"}]
    assert sent["temperature"] == 1.0  # clamped (D15)
    assert sent["stop_sequences"] == ["END"]
    assert sent["max_tokens"] == 1024


async def test_request_id_is_reused(gateway, upstream):
    key = await gateway.create_key()
    r = await gateway.client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {key}", "x-request-id": "my-trace.1"},
    )
    assert r.headers["x-request-id"] == "my-trace.1"


async def test_auth_errors(gateway, upstream):
    r = await gateway.chat("gw-not-a-real-key")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"
    r = await gateway.client.post(
        "/v1/chat/completions", json={"model": "gpt", "messages": [{"role": "user"}]}
    )
    assert r.status_code == 401
    assert upstream.requests == []
    assert await gateway.usage_rows() == []  # failed auth writes no usage row


async def test_revoked_key_is_rejected(gateway):
    key = await gateway.create_key()
    key_id = await gateway.client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert key_id.status_code == 200
    async with gateway.deps.store.db.execute("SELECT id FROM api_keys") as cur:
        (kid,) = await cur.fetchone()
    r = await gateway.client.delete(f"/admin/keys/{kid}", headers=gateway.admin)
    assert r.status_code == 204
    assert (await gateway.chat(key)).status_code == 401


async def test_model_allow_list(gateway, upstream):
    key = await gateway.create_key(allowed_models=["claude"])
    r = await gateway.chat(key, model="gpt")
    assert r.status_code == 403
    models = await gateway.client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert [m["id"] for m in models.json()["data"]] == ["claude"]


async def test_unknown_model_and_bad_body(gateway):
    key = await gateway.create_key()
    r = await gateway.chat(key, model="nope")
    assert r.status_code == 400 and r.json()["error"]["code"] == "model_not_found"
    r = await gateway.client.post(
        "/v1/chat/completions", content=b"{not json", headers={"Authorization": f"Bearer {key}"}
    )
    assert r.status_code == 400


async def test_tools_skip_incapable_targets(gateway, upstream):
    key = await gateway.create_key()
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    r = await gateway.chat(key, model="claude", tools=tools)
    assert r.status_code == 400
    assert upstream.requests == []
    r = await gateway.chat(key, model="fast", tools=tools)  # openai can serve it
    assert r.status_code == 200
    assert upstream.requests[0]["body"]["tools"] == tools


async def test_rate_limit(gateway, upstream):
    key = await gateway.create_key(rpm_limit=2)
    assert (await gateway.chat(key)).status_code == 200
    assert (await gateway.chat(key)).status_code == 200
    r = await gateway.chat(key)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    rows = await gateway.wait_for_rows(3)
    assert [row["status"] for row in rows] == [200, 200, 429]


async def test_budget(gateway, upstream):
    key = await gateway.create_key(monthly_budget_usd=0.00001)
    upstream.script(chunks(1), usage(10, 1))  # costs $0.000012 > budget
    assert (await gateway.chat(key)).status_code == 200
    r = await gateway.chat(key)
    assert r.status_code == 402
    assert r.json()["error"]["type"] == "insufficient_quota"
    assert len(upstream.requests) == 1


async def test_cache_hit_and_bypass(gateway, upstream):
    key = await gateway.create_key()
    first = await gateway.chat(key, temperature=0)
    assert first.headers["x-gateway-cache"] == "miss"
    second = await gateway.chat(key, temperature=0)
    assert second.headers["x-gateway-cache"] == "hit"
    assert second.json()["choices"] == first.json()["choices"]
    assert second.json()["id"] != first.json()["id"]
    assert len(upstream.requests) == 1
    r = await gateway.client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": "hello"}], "temperature": 0},
        headers={"Authorization": f"Bearer {key}", "x-gateway-cache": "bypass"},
    )
    assert r.headers["x-gateway-cache"] == "bypass"
    assert len(upstream.requests) == 2
    rows = await gateway.wait_for_rows(3)
    assert [(row["cache_hit"], row["cost_usd"] == 0) for row in rows] == [
        (0, False),
        (1, True),
        (0, False),
    ]


async def test_cache_scoped_per_key(gateway, upstream):
    a, b = await gateway.create_key(name="a"), await gateway.create_key(name="b")
    await gateway.chat(a, temperature=0)
    r = await gateway.chat(b, temperature=0)
    assert r.headers["x-gateway-cache"] == "miss"
    assert len(upstream.requests) == 2


async def test_no_cache_without_temperature_zero(gateway, upstream):
    key = await gateway.create_key()
    await gateway.chat(key)
    r = await gateway.chat(key)
    assert "x-gateway-cache" not in r.headers
    assert len(upstream.requests) == 2


async def test_metrics_endpoint(gateway, upstream):
    key = await gateway.create_key()
    await gateway.chat(key)
    text = (await gateway.client.get("/metrics")).text
    assert 'gateway_requests_total{alias="gpt",provider="openai",status_class="2xx"}' in text
    assert "gateway_ttft_seconds_bucket" in text


async def test_budget_is_a_soft_cap_under_concurrency(gateway, upstream):
    """D8: concurrent requests all pass the check before any of them records its cost."""
    key = await gateway.create_key(monthly_budget_usd=0.00001)
    for _ in range(3):
        upstream.script(wait(0.3), chunks(1), usage(10, 1))  # each costs $0.000012
    results = await asyncio.gather(*(gateway.chat(key) for _ in range(3)))
    assert [r.status_code for r in results] == [200, 200, 200]  # overspent 3.6x the budget
    assert (await gateway.chat(key)).status_code == 402
