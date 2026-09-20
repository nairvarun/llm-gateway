"""UTC period identity and exact decimal liability helpers."""

from datetime import UTC, datetime
from decimal import Decimal


def utc_period_starts(at: datetime) -> tuple[datetime, datetime]:
    current = at.astimezone(UTC)
    return (
        current.replace(hour=0, minute=0, second=0, microsecond=0),
        current.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
    )


def liability(state: str, reserved: Decimal, charged: Decimal) -> Decimal:
    return reserved if state == "held" else charged
