"""Stages tested alone, each with a fake call_next."""

from __future__ import annotations

import pytest

from gateway.config import CacheCfg
from gateway.context import GatewayResponse, RequestContext, Usage
from gateway.errors import BudgetExceeded, Forbidden, RateLimited, Unauthorized
from gateway.limiter.memory import MemoryLimiter
from gateway.schemas import ChatRequest
from gateway.stages.auth import Auth
from gateway.stages.base import build_chain
from gateway.stages.budget import Budget
from gateway.stages.cache import Cache, cache_key
from gateway.stages.rate_limit import RateLimit
from gateway.store.base import VirtualKey
from gateway.store.sqlite import hash_key
from tests.fakes.clock import FakeClock


def make_ctx(key: VirtualKey | None = None, **body) -> RequestContext:
    body.setdefault("model", "gpt")
    body.setdefault("messages", [{"role": "user", "content": "hi"}])
    req = ChatRequest(**body)
    return RequestContext(request_id="req_1", body=req, alias=req.model, key=key)


def vkey(**kw) -> VirtualKey:
    base = dict(
        id="key_1",
        name="k",
        allowed_models=None,
        rpm_limit=None,
        monthly_budget_usd=None,
        created_at="2026-01-01T00:00:00.000000Z",
    )
    return VirtualKey(**(base | kw))


async def ok(ctx: RequestContext) -> GatewayResponse:
    return GatewayResponse(body={"id": "x", "choices": []})


class FakeStore:
    def __init__(self, key: VirtualKey | None = None, spent: float = 0.0) -> None:
        self.key, self.spent = key, spent

    async def get_key_by_hash(self, h: str) -> VirtualKey | None:
        return self.key if self.key and h == hash_key("gw-good") else None

    async def spend_this_month(self, key_id: str) -> float:
        return self.spent


async def test_build_chain_order():
    calls: list[str] = []

    def stage(name):
        async def s(ctx, call_next):
            calls.append(f"{name} in")
            resp = await call_next(ctx)
            calls.append(f"{name} out")
            return resp

        return s

    await build_chain([stage("a"), stage("b")], ok)(make_ctx())
    assert calls == ["a in", "b in", "b out", "a out"]


async def test_auth():
    auth = Auth(FakeStore(vkey(allowed_models=["claude"])))
    with pytest.raises(Unauthorized):
        await auth(make_ctx(), ok)
    ctx = make_ctx()
    ctx.bearer = "gw-bad"
    with pytest.raises(Unauthorized):
        await auth(ctx, ok)
    ctx = make_ctx()
    ctx.bearer = "gw-good"
    with pytest.raises(Forbidden):
        await auth(ctx, ok)
    ctx = make_ctx(model="claude")
    ctx.bearer = "gw-good"
    await auth(ctx, ok)
    assert ctx.key.id == "key_1" and ctx.bearer is None


async def test_revoked_key_is_unauthorized():
    auth = Auth(FakeStore(vkey(revoked_at="2026-01-02T00:00:00.000000Z")))
    ctx = make_ctx()
    ctx.bearer = "gw-good"
    with pytest.raises(Unauthorized):
        await auth(ctx, ok)


async def test_rate_limit_stage():
    stage = RateLimit(MemoryLimiter(now=FakeClock().now))
    await stage(make_ctx(vkey()), ok)  # no limit set
    limited = vkey(rpm_limit=1)
    await stage(make_ctx(limited), ok)
    with pytest.raises(RateLimited) as e:
        await stage(make_ctx(limited), ok)
    assert e.value.retry_after == 60


async def test_budget_stage():
    await Budget(FakeStore(spent=5.0))(make_ctx(vkey()), ok)  # no budget set
    await Budget(FakeStore(spent=0.5))(make_ctx(vkey(monthly_budget_usd=1.0)), ok)
    with pytest.raises(BudgetExceeded):
        await Budget(FakeStore(spent=1.0))(make_ctx(vkey(monthly_budget_usd=1.0)), ok)


def test_cache_key_ignores_stream_and_user():
    a = make_ctx(temperature=0).body
    b = make_ctx(temperature=0, user="someone", stream_options={"include_usage": True}).body
    assert cache_key("k", "gpt", a) == cache_key("k", "gpt", b)
    assert cache_key("k", "gpt", a) != cache_key("other", "gpt", a)
    assert cache_key("k", "gpt", a) != cache_key("k", "claude", a)


async def test_cache_ttl_and_lru():
    clock = FakeClock()
    cache = Cache(CacheCfg(max_entries=2, ttl_s=10), now=clock.now)
    cache.put("a", {"v": 1}, Usage(1, 1))
    cache.put("b", {"v": 2}, None)
    assert cache.get("a") is not None  # touch a, so b is now least recent
    cache.put("c", {"v": 3}, None)
    assert cache.get("b") is None and cache.get("a") is not None
    clock.advance(11)
    assert cache.get("a") is None  # expired


async def test_cache_skips_streams_and_nonzero_temperature():
    calls = 0

    async def counting(ctx):
        nonlocal calls
        calls += 1
        return await ok(ctx)

    cache = Cache(CacheCfg())
    for body in ({"temperature": 0.5}, {}, {"temperature": 0, "stream": True}):
        await cache(make_ctx(vkey(), **body), counting)
        await cache(make_ctx(vkey(), **body), counting)
    assert calls == 6
