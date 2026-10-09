"""Stream wrapping: observe a stream without buffering it, and clean up exactly once.

Cleanup must survive cancellation. Under anyio, a cancelled task is cancelled again at every
await, so any await in a `finally` block goes through `shielded`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

from gateway.errors import GatewayError
from gateway.schemas import ChatChunk

log = logging.getLogger("gateway.streams")

Outcome = Literal["completed", "error", "cancelled"]


@dataclass
class StreamError:
    """Yielded once when a stream fails after the first byte; the stream then ends."""

    error: GatewayError


StreamItem = ChatChunk | StreamError

_background: set[asyncio.Task] = set()


async def shielded(coro: Coroutine[Any, Any, None]) -> None:
    """Run coro to completion even if the caller is cancelled."""
    task = asyncio.ensure_future(coro)
    _background.add(task)  # keep a reference so it is not garbage-collected
    task.add_done_callback(_background.discard)
    await asyncio.shield(task)


async def drain_background(wait_s: float) -> None:
    """Called at shutdown so pending usage writes finish."""
    if _background:
        await asyncio.wait(set(_background), timeout=wait_s)


async def wrap_stream(
    source: AsyncIterator[StreamItem],
    on_item: Callable[[StreamItem], None] | None,
    on_close: Callable[[Outcome], Awaitable[None]],
) -> AsyncIterator[StreamItem]:
    outcome: Outcome = "completed"
    try:
        async for item in source:
            if on_item is not None:
                on_item(item)  # cheap, synchronous bookkeeping only
            yield item
    except (asyncio.CancelledError, GeneratorExit):
        outcome = "cancelled"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:

        async def close() -> None:
            # Close the inner stream first, so inner cleanup runs before ours.
            aclose = getattr(source, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    log.exception("closing inner stream failed")
            try:
                await on_close(outcome)
            except Exception:
                log.exception("stream on_close failed")

        await shielded(close())
