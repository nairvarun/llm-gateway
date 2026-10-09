"""The Store interface. Nothing outside gateway/store imports a database driver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class VirtualKey:
    id: str
    name: str
    allowed_models: list[str] | None
    rpm_limit: int | None
    monthly_budget_usd: float | None
    created_at: str
    revoked_at: str | None = None


@dataclass
class UsageRow:
    request_id: str
    key_id: str
    ts: str
    model_alias: str
    provider: str | None
    model: str | None
    attempts: int
    input_tokens: int | None
    output_tokens: int | None
    usage_estimated: bool
    cost_usd: float
    latency_ms: int | None
    ttft_ms: int | None
    status: int
    cache_hit: bool


class Store(Protocol):
    async def get_key_by_hash(self, hash: str) -> VirtualKey | None: ...

    async def get_key(self, key_id: str) -> VirtualKey | None: ...

    async def create_key(
        self,
        name: str,
        allowed_models: list[str] | None,
        rpm_limit: int | None,
        monthly_budget_usd: float | None,
    ) -> tuple[VirtualKey, str]: ...

    async def revoke_key(self, key_id: str) -> bool: ...

    async def record_usage(self, row: UsageRow) -> None: ...

    async def spend_this_month(self, key_id: str) -> float: ...

    async def usage_summary(self, key_id: str) -> dict: ...

    async def ping(self) -> bool: ...


def utc_ts(dt: datetime | None = None) -> str:
    """One fixed format, so string comparison sorts by time."""
    return (dt or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def month_start(dt: datetime | None = None) -> datetime:
    dt = dt or datetime.now(UTC)
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
