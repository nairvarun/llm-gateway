class FakeClock:
    """A monotonic clock tests can move by hand, so 30-second waits run instantly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds
