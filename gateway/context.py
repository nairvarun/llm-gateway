"""The per-request state every stage reads and writes, and the response stages pass back."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from gateway.schemas import ChatRequest

if TYPE_CHECKING:
    from gateway.errors import GatewayError
    from gateway.store.base import VirtualKey
    from gateway.streams import StreamItem


@dataclass(frozen=True)
class Target:
    provider: str  # "openai" | "anthropic"
    model: str  # provider model ID


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    estimated: bool = False


@dataclass
class RequestContext:
    request_id: str
    body: ChatRequest
    alias: str  # == body.model
    bearer: str | None = None  # raw Authorization token; auth clears it
    key: VirtualKey | None = None
    target: Target | None = None
    attempts: int = 0
    started_at: float = field(default_factory=time.monotonic)
    first_byte_at: float | None = None
    usage: Usage | None = None
    cache_mode: str = "default"  # "default" | "bypass", from x-gateway-cache
    cache_hit: bool = False
    status: int = 200
    error: GatewayError | None = None  # set when a stream ends in error
    output_chars: int = 0  # for token estimation
    span: Any = None  # tracing span, when tracing is on


@dataclass
class GatewayResponse:
    status: int = 200
    body: dict | None = None  # non-streaming
    stream: AsyncIterator[StreamItem] | None = None  # streaming
    headers: dict[str, str] = field(default_factory=dict)
