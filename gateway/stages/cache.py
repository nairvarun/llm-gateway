"""Exact-match cache for non-streaming requests that set temperature to 0.

Entries are scoped per key, so one key never receives a response generated for another.
"""

from __future__ import annotations

import copy
import json
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from hashlib import sha256

from gateway import metrics
from gateway.config import CacheCfg
from gateway.context import GatewayResponse, RequestContext, Usage
from gateway.schemas import ChatRequest
from gateway.stages.base import Next

IGNORED_FIELDS = ("stream", "stream_options", "user")


def cache_key(key_id: str, alias: str, body: ChatRequest) -> str:
    data = {k: v for k, v in body.payload().items() if k not in IGNORED_FIELDS}
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return sha256(f"{key_id}\n{alias}\n{canonical}".encode()).hexdigest()


class Cache:
    def __init__(self, cfg: CacheCfg, now: Callable[[], float] = time.monotonic) -> None:
        self.cfg, self.now = cfg, now
        self.entries: OrderedDict[str, tuple[float, dict, Usage | None]] = OrderedDict()

    def get(self, k: str) -> tuple[dict, Usage | None] | None:
        entry = self.entries.get(k)
        if entry is None:
            return None
        expires_at, body, usage = entry
        if expires_at <= self.now():
            del self.entries[k]
            return None
        self.entries.move_to_end(k)
        return body, usage

    def put(self, k: str, body: dict, usage: Usage | None) -> None:
        self.entries[k] = (self.now() + self.cfg.ttl_s, body, usage)
        self.entries.move_to_end(k)
        while len(self.entries) > self.cfg.max_entries:
            self.entries.popitem(last=False)

    async def __call__(self, ctx: RequestContext, call_next: Next) -> GatewayResponse:
        eligible = not ctx.body.stream and ctx.body.temperature == 0
        if not eligible:
            return await call_next(ctx)
        if ctx.cache_mode == "bypass":
            metrics.cache_requests.labels("bypass").inc()
            resp = await call_next(ctx)
            resp.headers["x-gateway-cache"] = "bypass"
            return resp

        k = cache_key(ctx.key.id, ctx.alias, ctx.body)
        hit = self.get(k)
        if hit is not None:
            body, usage = hit
            metrics.cache_requests.labels("hit").inc()
            ctx.cache_hit, ctx.usage = True, usage
            body = copy.deepcopy(body)
            body["id"] = "chatcmpl-" + secrets.token_hex(12)
            return GatewayResponse(body=body, headers={"x-gateway-cache": "hit"})

        metrics.cache_requests.labels("miss").inc()
        resp = await call_next(ctx)
        if resp.status == 200 and resp.body is not None:
            self.put(k, copy.deepcopy(resp.body), ctx.usage)
        resp.headers["x-gateway-cache"] = "miss"
        return resp
