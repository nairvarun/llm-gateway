"""OpenAI adapter: a near passthrough, since the internal format is OpenAI's."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from pydantic import ValidationError

from gateway.config import ProviderCfg
from gateway.providers.base import (
    ProviderError,
    http_error,
    make_client,
    parse_json,
    transport_error,
)
from gateway.schemas import ChatChunk, ChatRequest, ChatResponse
from gateway.sse import parse_sse


class OpenAIProvider:
    name = "openai"
    supports_tools = True

    def __init__(
        self,
        cfg: ProviderCfg,
        api_key: str,
        connect_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"}
        self.client = make_client(cfg.base_url, headers, connect_s, transport)

    def payload(self, req: ChatRequest, model: str, stream: bool) -> dict:
        body = req.payload()
        body["model"] = model
        body["stream"] = stream
        if stream:
            # Always ask for usage; route strips the usage chunk if the client did not ask.
            body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
        else:
            body.pop("stream_options", None)
        return body

    async def complete(self, req: ChatRequest, model: str) -> ChatResponse:
        try:
            resp = await self.client.post(
                "/chat/completions", json=self.payload(req, model, stream=False)
            )
        except httpx.TransportError as e:
            raise transport_error(e) from e
        if resp.status_code >= 400:
            raise await http_error(resp)
        try:
            return ChatResponse.model_validate(parse_json(resp.text))
        except ValidationError as e:
            raise ProviderError("protocol", message="unexpected response shape") from e

    async def stream(self, req: ChatRequest, model: str) -> AsyncIterator[ChatChunk]:
        body = self.payload(req, model, stream=True)
        try:
            async with self.client.stream("POST", "/chat/completions", json=body) as resp:
                if resp.status_code >= 400:
                    raise await http_error(resp)
                async for ev in parse_sse(resp.aiter_lines()):
                    if ev.data == "[DONE]":
                        return
                    try:
                        yield ChatChunk.model_validate(parse_json(ev.data))
                    except ValidationError as e:
                        raise ProviderError("protocol", message="unexpected chunk shape") from e
                raise ProviderError("protocol", message="stream ended without [DONE]")
        except httpx.TransportError as e:
            raise transport_error(e) from e

    async def aclose(self) -> None:
        await self.client.aclose()
