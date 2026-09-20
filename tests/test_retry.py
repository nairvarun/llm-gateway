from datetime import UTC, datetime
from random import Random

import pytest

from app.domain.models import FailureKind, ProviderFailure
from app.domain.retry import parse_retry_after, retry_delay


def test_retry_after_delta_and_date() -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    assert parse_retry_after("3.5", now=now) == 3.5
    assert parse_retry_after("Sun, 20 Sep 2026 00:00:05 GMT", now=now) == 5
    assert parse_retry_after("not-a-date", now=now) is None
    assert parse_retry_after("-2", now=now) is None
    assert parse_retry_after("9" * 129, now=now) is None


def test_retry_backoff_is_bounded_and_seed_repeatable() -> None:
    failure = ProviderFailure(FailureKind.SERVER)
    first, second = Random(123), Random(123)
    values = [retry_delay(failure, index, first) for index in range(1, 10)]
    assert values == [retry_delay(failure, index, second) for index in range(1, 10)]
    assert all(0 <= value <= min(2.0, 0.1 * 2**index) for index, value in enumerate(values))


def test_retry_after_floor_and_permanent_failure() -> None:
    failure = ProviderFailure(FailureKind.RATE_LIMIT, retry_after_seconds=10)
    assert retry_delay(failure, 1, Random(0)) == 10
    with pytest.raises(ValueError):
        retry_delay(ProviderFailure(FailureKind.INVALID_REQUEST), 1, Random(0))
    assert (
        ProviderFailure(FailureKind.SERVER, retry_after_seconds=float("inf")).retry_after_seconds
        is None
    )
