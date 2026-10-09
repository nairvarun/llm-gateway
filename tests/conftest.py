from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from gateway.app import Deps, create_app
from gateway.config import Config, Settings
from tests.fakes.servers import RunningServer, start_server
from tests.fakes.upstream import FakeUpstream

ADMIN_TOKEN = "admin-" + "x" * 40
ENV = {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "ak-test"}


def config_dict(upstream_url: str = "http://upstream.invalid") -> dict[str, Any]:
    return {
        "providers": {
            "openai": {"base_url": f"{upstream_url}/v1", "api_key_env": "OPENAI_API_KEY"},
            "anthropic": {"base_url": upstream_url, "api_key_env": "ANTHROPIC_API_KEY"},
        },
        "models": {
            "fast": {
                "targets": [
                    {"provider": "openai", "model": "gpt-test"},
                    {"provider": "anthropic", "model": "claude-test"},
                ]
            },
            "gpt": {"targets": [{"provider": "openai", "model": "gpt-test"}]},
            "claude": {"targets": [{"provider": "anthropic", "model": "claude-test"}]},
        },
        "pricing": {
            "gpt-test": {"input": 1.0, "output": 2.0},
            "claude-test": {"input": 3.0, "output": 15.0},
        },
        "timeouts": {"connect_s": 2, "first_byte_s": 5, "idle_s": 5, "total_s": 30},
        "retries": {"max_attempts": 3, "backoff_base_s": 0.01},
        "shutdown": {"drain_s": 30, "drain_delay_s": 0},
    }


def merge(base: dict, overrides: dict) -> dict:
    out = dict(base)
    for k, v in overrides.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def make_config(upstream_url: str = "http://upstream.invalid", **overrides: Any) -> Config:
    return Config.model_validate(merge(config_dict(upstream_url), overrides))


@dataclass
class Gateway:
    url: str
    deps: Deps
    client: httpx.AsyncClient
    server: RunningServer
    admin: dict[str, str] = field(
        default_factory=lambda: {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    )

    async def create_key(self, **body: Any) -> str:
        body.setdefault("name", "test")
        r = await self.client.post("/admin/keys", json=body, headers=self.admin)
        assert r.status_code == 201, r.text
        return r.json()["key"]

    async def chat(self, key: str, **body: Any) -> httpx.Response:
        body.setdefault("model", "gpt")
        body.setdefault("messages", [{"role": "user", "content": "hello"}])
        return await self.client.post(
            "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}
        )

    async def usage_rows(self) -> list[dict]:
        async with self.deps.store.db.execute("SELECT * FROM usage ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def wait_for_rows(self, n: int, timeout: float = 3.0) -> list[dict]:
        async with asyncio.timeout(timeout):
            while len(rows := await self.usage_rows()) < n:
                await asyncio.sleep(0.02)
        return rows


def sse_events(lines: list[str]) -> list[Any]:
    """Data payloads from SSE lines: dicts for JSON events, the string for [DONE]."""
    out = []
    for line in lines:
        if line.startswith("data: "):
            data = line[len("data: ") :]
            out.append(data if data == "[DONE]" else json.loads(data))
    return out


@pytest.fixture
async def upstream() -> AsyncIterator[FakeUpstream]:
    fake = FakeUpstream()
    server = await start_server(fake)
    fake.url = server.url
    yield fake
    await server.stop()


@pytest.fixture
async def start_gateway(upstream: FakeUpstream, tmp_path):
    started: list[Gateway] = []

    async def start(**overrides: Any) -> Gateway:
        cfg = make_config(upstream.url, **overrides)
        settings = Settings(
            admin_token=ADMIN_TOKEN, gateway_db_path=str(tmp_path / f"gw{len(started)}.db")
        )
        app = create_app(cfg, settings, environ=ENV)
        server = await start_server(app)
        client = httpx.AsyncClient(base_url=server.url, timeout=10)
        gw = Gateway(server.url, app.state.deps, client, server)
        started.append(gw)
        return gw

    yield start
    for gw in started:
        await gw.client.aclose()
        await gw.server.stop()


@pytest.fixture
async def gateway(start_gateway) -> Gateway:
    return await start_gateway()
