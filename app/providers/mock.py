import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.models import (
    FailureKind,
    FinishReason,
    ProviderCapabilities,
    ProviderFailure,
    ProviderInput,
    ProviderResult,
    TokenUsage,
)

SCENARIOS = frozenset(
    {
        "success",
        "timeout",
        "connection",
        "rate_limit",
        "server",
        "server_permanent",
        "credential",
        "invalid_request",
        "malformed",
        "schema_invalid",
        "refusal",
        "truncation",
        "missing_usage",
    }
)


@dataclass(frozen=True)
class MockStep:
    scenario: str = "success"
    output: str | None = None
    delay_seconds: float = 0
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.scenario not in SCENARIOS or self.delay_seconds < 0:
            raise ValueError("Invalid mock step")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("Invalid Retry-After")


class MockProvider:
    """Consume a fixed sequence; repeat the last step once the sequence ends.

    Steps are controlled by local configuration/test injection, never client
    metadata. Extraction echoes JSON input; this is not a real language model.
    """

    def __init__(
        self, steps: Sequence[MockStep] = (MockStep(),), *, model: str = "mock-text-v1"
    ) -> None:
        if not steps:
            raise ValueError("At least one mock step is required")
        if not model or len(model) > 100:
            raise ValueError("Invalid mock model identity")
        self._steps = tuple(steps)
        self.model = model
        self.invocations = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("mock", self.model, True, 100_000, 16_384)

    async def invoke(self, request: ProviderInput) -> ProviderResult:
        step = self._steps[min(self.invocations, len(self._steps) - 1)]
        self.invocations += 1
        if step.delay_seconds:
            await asyncio.sleep(step.delay_seconds)
        if step.scenario in {kind.value for kind in FailureKind}:
            raise ProviderFailure(
                FailureKind(step.scenario), retry_after_seconds=step.retry_after_seconds
            )
        output = step.output
        if output is None:
            output = (
                request.text if request.schema is not None else "Mock response: " + request.text
            )
        finish_reason = FinishReason.STOP
        if step.scenario == "malformed":
            output = "{invalid json"
        elif step.scenario == "schema_invalid":
            output = json.dumps({"unexpected": True})
        elif step.scenario == "refusal":
            output, finish_reason = "Mock refusal", FinishReason.REFUSAL
        elif step.scenario == "truncation":
            finish_reason = FinishReason.LENGTH
        if len(output) > request.max_output_tokens * 4:
            output = output[: request.max_output_tokens * 4]
            finish_reason = FinishReason.LENGTH
        # Synthetic tokenizer: one token per four Unicode code points (rounded
        # up), consistent with this mock's output cap. Not vendor token usage.
        usage = TokenUsage((len(request.text) + 3) // 4, (len(output) + 3) // 4, "synthetic")
        if step.scenario == "missing_usage":
            usage = TokenUsage(None, None, "unknown")
        return ProviderResult(output, "mock", self.model, finish_reason, usage)
