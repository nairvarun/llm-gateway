"""Anthropic adapter: translates OpenAI-shaped requests to the Messages API and back.

It translates the system prompt, max_tokens, stop sequences, stop reasons and stream events,
and nothing else. Tool calls are not supported (supports_tools = False), so route never sends
a request with tools here.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from gateway.config import ProviderCfg
from gateway.errors import BadRequest
from gateway.providers.base import (
    ProviderError,
    http_error,
    make_client,
    parse_json,
    transport_error,
)
from gateway.schemas import ChatChunk, ChatRequest, ChatResponse
from gateway.sse import parse_sse

log = logging.getLogger("gateway.providers.anthropic")

API_VERSION = "2023-06-01"
STOP_REASONS = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}


def _content(content: Any) -> str | list[dict[str, str]]:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text":
                raise BadRequest("only text content is supported for this model")
            blocks.append({"type": "text", "text": part.get("text", "")})
        return blocks
    raise BadRequest("message content must be a string or a list of parts")


def to_anthropic(req: ChatRequest, model: str, default_max_tokens: int, stream: bool) -> dict:
    system: list[str] = []
    messages: list[dict[str, Any]] = []
    for m in req.messages:
        role = m.get("role")
        if role in ("system", "developer"):
            text = _content(m.get("content"))
            system.append(text if isinstance(text, str) else "".join(b["text"] for b in text))
        elif role in ("user", "assistant"):
            if m.get("tool_calls"):
                raise BadRequest("tool calls are not supported for this model")
            messages.append({"role": role, "content": _content(m.get("content"))})
        else:
            raise BadRequest(f"message role {role!r} is not supported for this model")

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": req.max_completion_tokens or req.max_tokens or default_max_tokens,
        "stream": stream,
    }
    if system:
        body["system"] = "\n\n".join(system)
    if req.temperature is not None:
        if req.temperature > 1:
            log.debug("clamping temperature %s to 1 for anthropic", req.temperature)
        body["temperature"] = min(req.temperature, 1.0)
    if req.top_p is not None:
        body["top_p"] = req.top_p
    if req.stop is not None:
        body["stop_sequences"] = [req.stop] if isinstance(req.stop, str) else req.stop
    if req.user is not None:
        body["metadata"] = {"user_id": req.user}
    return body


def _usage(input_tokens: int, output_tokens: int) -> dict[str, int]:
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def from_anthropic(data: dict, model: str) -> ChatResponse:
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    u = data.get("usage") or {}
    return ChatResponse(
        id=data.get("id", ""),
        created=int(time.time()),
        model=model,
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": STOP_REASONS.get(data.get("stop_reason"), "stop"),
            }
        ],
        usage=_usage(u.get("input_tokens", 0), u.get("output_tokens", 0)),
    )


class AnthropicProvider:
    name = "anthropic"
    supports_tools = False

    def __init__(
        self,
        cfg: ProviderCfg,
        api_key: str,
        connect_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"x-api-key": api_key, "anthropic-version": API_VERSION}
        self.client = make_client(cfg.base_url, headers, connect_s, transport)
        self.default_max_tokens = cfg.default_max_tokens

    async def complete(self, req: ChatRequest, model: str) -> ChatResponse:
        body = to_anthropic(req, model, self.default_max_tokens, stream=False)
        try:
            resp = await self.client.post("/v1/messages", json=body)
        except httpx.TransportError as e:
            raise transport_error(e) from e
        if resp.status_code >= 400:
            raise await http_error(resp)
        return from_anthropic(parse_json(resp.text), model)

    async def stream(self, req: ChatRequest, model: str) -> AsyncIterator[ChatChunk]:
        body = to_anthropic(req, model, self.default_max_tokens, stream=True)
        msg_id, created, input_tokens, output_tokens = "", int(time.time()), 0, 0

        def chunk(delta: dict | None = None, finish: str | None = None) -> ChatChunk:
            choices = [{"index": 0, "delta": delta or {}, "finish_reason": finish}]
            return ChatChunk(id=msg_id, created=created, model=model, choices=choices)

        try:
            async with self.client.stream("POST", "/v1/messages", json=body) as resp:
                if resp.status_code >= 400:
                    raise await http_error(resp)
                async for ev in parse_sse(resp.aiter_lines()):
                    data = parse_json(ev.data)
                    kind = data.get("type")
                    if kind == "message_start":
                        msg = data.get("message") or {}
                        msg_id = msg.get("id", "")
                        input_tokens = (msg.get("usage") or {}).get("input_tokens", 0)
                        yield chunk({"role": "assistant", "content": ""})
                    elif kind == "content_block_delta":
                        delta = data.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield chunk({"content": delta.get("text", "")})
                    elif kind == "message_delta":
                        output_tokens = (data.get("usage") or {}).get("output_tokens", 0)
                        reason = (data.get("delta") or {}).get("stop_reason")
                        yield chunk(finish=STOP_REASONS.get(reason, "stop"))
                    elif kind == "message_stop":
                        yield ChatChunk(
                            id=msg_id,
                            created=created,
                            model=model,
                            choices=[],
                            usage=_usage(input_tokens, output_tokens),
                        )
                        return
                    elif kind == "error":
                        err = data.get("error") or {}
                        overloaded = err.get("type") == "overloaded_error"
                        raise ProviderError(
                            "http",
                            status=529 if overloaded else 500,
                            message=err.get("message", ""),
                        )
                    # content_block_start, content_block_stop and ping carry nothing we need.
                raise ProviderError("protocol", message="stream ended without message_stop")
        except httpx.TransportError as e:
            raise transport_error(e) from e

    async def aclose(self) -> None:
        await self.client.aclose()
