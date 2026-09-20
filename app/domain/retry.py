"""Bounded classified retry timing, independent of provider SDKs."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import isfinite
from random import Random

from app.domain.models import FailureKind, ProviderFailure

RETRYABLE = frozenset(
    {FailureKind.TIMEOUT, FailureKind.CONNECTION, FailureKind.RATE_LIMIT, FailureKind.SERVER}
)


def parse_retry_after(header: str | None, *, now: datetime | None = None) -> float | None:
    if header is None or len(header) > 128:
        return None
    try:
        seconds = float(header)
        if isfinite(seconds) and seconds >= 0:
            return seconds
    except ValueError:
        pass
    try:
        date = parsedate_to_datetime(header)
        if date.tzinfo is None:
            return None
        seconds = (date - (now or datetime.now(UTC))).total_seconds()
        return max(0.0, seconds)
    except (TypeError, ValueError, OverflowError):
        return None


def retry_delay(
    failure: ProviderFailure,
    retry_index: int,
    random: Random,
    *,
    base_seconds: float = 0.1,
    cap_seconds: float = 2.0,
) -> float:
    """Full jitter plus Retry-After floor, capped by the caller's deadline."""
    if failure.kind not in RETRYABLE or retry_index < 1:
        raise ValueError("This failure is not retryable")
    maximum = min(cap_seconds, base_seconds * (2 ** (retry_index - 1)))
    jitter = random.uniform(0, maximum)
    return max(jitter, failure.retry_after_seconds or 0.0)
