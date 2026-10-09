"""Monthly spend cap per key, checked before the call.

This is a soft cap (spec D8): concurrent requests can all pass the check before any of them
records its cost. A hard cap would need reservations.
"""

from __future__ import annotations

from gateway import metrics
from gateway.context import GatewayResponse, RequestContext
from gateway.errors import BudgetExceeded
from gateway.stages.base import Next
from gateway.store.base import Store


class Budget:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def __call__(self, ctx: RequestContext, call_next: Next) -> GatewayResponse:
        budget = ctx.key.monthly_budget_usd
        if budget is not None:
            spent = await self.store.spend_this_month(ctx.key.id)
            if spent >= budget:
                metrics.budget_exceeded.inc()
                raise BudgetExceeded(f"monthly budget of ${budget:.2f} is used up")
        return await call_next(ctx)
