"""Circuit breaker per provider: closed -> open after N straight failures -> half-open -> ...

No lock is needed: none of these methods await.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

from gateway import metrics

State = Literal["closed", "open", "half_open"]
_GAUGE = {"closed": 0, "half_open": 1, "open": 2}


class Breaker:
    def __init__(
        self,
        provider: str,
        threshold: int,
        open_s: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider, self.threshold, self.open_s, self.now = provider, threshold, open_s, now
        self.failures = 0
        self.opened_at = 0.0
        self.trial_in_flight = False
        self._set("closed")

    def _set(self, state: State) -> None:
        self.state: State = state
        metrics.breaker_state.labels(self.provider).set(_GAUGE[state])

    def can_try(self) -> bool:
        """Pure check used to build the list of available targets."""
        if self.state == "open" and self.now() - self.opened_at >= self.open_s:
            self._set("half_open")
            self.trial_in_flight = False
        if self.state == "half_open":
            return not self.trial_in_flight
        return self.state == "closed"

    def begin(self) -> None:
        """Called only for the target actually chosen: claims the half-open trial slot."""
        if self.state == "half_open":
            self.trial_in_flight = True

    def record_success(self) -> None:
        self.failures, self.trial_in_flight = 0, False
        if self.state != "closed":
            self._set("closed")

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.threshold:
            self.opened_at, self.trial_in_flight = self.now(), False
            self._set("open")

    def release(self) -> None:
        """Neutral outcome (429, client cancel): frees the trial slot, changes nothing else."""
        self.trial_in_flight = False
