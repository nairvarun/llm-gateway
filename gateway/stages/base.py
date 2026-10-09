"""The stage contract and how the chain is built from a list."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Protocol

from gateway.context import GatewayResponse, RequestContext

Next = Callable[[RequestContext], Awaitable[GatewayResponse]]


class Stage(Protocol):
    async def __call__(self, ctx: RequestContext, call_next: Next) -> GatewayResponse: ...


def build_chain(stages: list[Stage], terminal: Next) -> Next:
    """stages[0] is outermost: it runs first and sees the response last."""
    handler = terminal
    for stage in reversed(stages):
        handler = functools.partial(stage, call_next=handler)
    return handler
