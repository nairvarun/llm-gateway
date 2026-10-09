"""What every provider adapter looks like, and how HTTP failures are classified."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Literal, Protocol

import httpx

from gateway.schemas import ChatChunk, ChatRequest, ChatResponse

ErrorKind = Literal["connect", "network", "timeout_first_byte", "timeout_idle", "http", "protocol"]


class ProviderError(Exception):
    def __init__(
        self,
        kind: ErrorKind,
        *,
        status: int | None = None,
        retry_after: float | None = None,
        message: str = "",
    ) -> None:
        super().__init__(f"{kind} {status or ''} {message}".strip())
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        self.message = message


class Provider(Protocol):
    name: str
    supports_tools: bool

    async def complete(self, req: ChatRequest, model: str) -> ChatResponse: ...

    def stream(self, req: ChatRequest, model: str) -> AsyncIterator[ChatChunk]: ...

    async def aclose(self) -> None: ...


def make_client(
    base_url: str,
    headers: dict[str, str],
    connect_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    # read=None: route enforces first-byte and idle timeouts itself.
    timeout = httpx.Timeout(connect=connect_s, read=None, write=10.0, pool=5.0)
    return httpx.AsyncClient(
        base_url=base_url, headers=headers, timeout=timeout, transport=transport
    )


def transport_error(e: httpx.TransportError) -> ProviderError:
    if isinstance(e, httpx.ConnectError | httpx.ConnectTimeout):
        return ProviderError("connect", message=str(e) or type(e).__name__)
    # A connection that broke after it was established (reset, truncated body, ...).
    return ProviderError("network", message=str(e) or type(e).__name__)


async def http_error(resp: httpx.Response) -> ProviderError:
    await resp.aread()
    message = resp.text[:500]
    try:
        err = resp.json().get("error")
        if isinstance(err, dict):
            message = str(err.get("message", message))
    except (ValueError, AttributeError):
        pass
    return ProviderError(
        "http",
        status=resp.status_code,
        retry_after=parse_retry_after(resp.headers.get("retry-after")),
        message=message,
    )


def parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form: ignore, use normal backoff


def parse_json(data: str) -> dict:
    try:
        obj = json.loads(data)
    except ValueError as e:
        raise ProviderError("protocol", message=f"invalid JSON: {data[:100]!r}") from e
    if not isinstance(obj, dict):
        raise ProviderError("protocol", message="expected a JSON object")
    return obj
