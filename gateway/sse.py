"""Server-sent events: parse what providers send, encode what clients receive."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

DONE = b"data: [DONE]\n\n"


@dataclass
class SSEEvent:
    event: str | None
    data: str


async def parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[SSEEvent]:
    """Turn a stream of text lines into events. A blank line ends an event."""
    event: str | None = None
    data: list[str] = []
    async for line in lines:
        if line == "":
            if data:
                yield SSEEvent(event, "\n".join(data))
            event, data = None, []
            continue
        if line.startswith(":"):
            continue  # comment / keep-alive
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if data:
        yield SSEEvent(event, "\n".join(data))


def encode_event(obj: dict) -> bytes:
    return b"data: " + json.dumps(obj, separators=(",", ":")).encode() + b"\n\n"
