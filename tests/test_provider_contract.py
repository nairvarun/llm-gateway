import pytest

from app.domain.models import FailureKind, FinishReason, ProviderFailure, ProviderInput
from app.providers.mock import MockProvider, MockStep


@pytest.mark.parametrize("kind", list(FailureKind))
async def test_classified_failures(kind: FailureKind) -> None:
    provider = MockProvider([MockStep(kind.value)])
    with pytest.raises(ProviderFailure) as error:
        await provider.invoke(ProviderInput("synthetic test"))
    assert error.value.kind == kind
    assert error.value.usage.input_tokens is None
    assert "synthetic test" not in str(error.value)


@pytest.mark.parametrize(
    ("scenario", "finish_reason"),
    [
        ("success", FinishReason.STOP),
        ("refusal", FinishReason.REFUSAL),
        ("truncation", FinishReason.LENGTH),
    ],
)
async def test_normalized_results(scenario: str, finish_reason: FinishReason) -> None:
    result = await MockProvider([MockStep(scenario)]).invoke(ProviderInput("hello"))
    assert result.provider == "mock"
    assert result.model == "mock-text-v1"
    assert result.finish_reason == finish_reason


async def test_repeatable_script_and_missing_usage() -> None:
    steps = [MockStep("malformed"), MockStep("missing_usage")]
    first, second = MockProvider(steps), MockProvider(steps)
    for _ in range(3):
        a, b = (
            await first.invoke(ProviderInput("hello")),
            await second.invoke(ProviderInput("hello")),
        )
        assert a == b
    assert a.usage.input_tokens is None
    assert a.usage.output_tokens is None


async def test_echo_extraction_and_output_bound() -> None:
    provider = MockProvider()
    result = await provider.invoke(ProviderInput('{"count": 2}', schema={"type": "object"}))
    assert result.output == '{"count": 2}'
    limited = await provider.invoke(ProviderInput("long synthetic input", max_output_tokens=1))
    assert len(limited.output) == 4
    assert limited.finish_reason == FinishReason.LENGTH
