import json
from pathlib import Path
from typing import Any, cast

import anthropic
import httpx2
import pytest

from app.domain.models import FailureKind, FinishReason, ProviderFailure, ProviderInput
from app.providers.anthropic_adapter import AnthropicProvider

FIXTURE = Path(__file__).parent / "fixtures" / "providers" / "anthropic" / "success.json"


def response_payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text()))


async def test_anthropic_normalizes_success_and_uses_one_sdk_attempt() -> None:
    count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        assert request.method == "POST"
        assert request.url.path == "/v1/messages"
        wire = json.loads(request.content)
        assert wire["model"] == "claude-haiku-4-5-20251001"
        assert wire["temperature"] == 0
        assert wire["max_tokens"] == 512
        assert wire["tools"] == []
        return httpx2.Response(200, json=response_payload())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as transport:
        client = anthropic.AsyncAnthropic(api_key="synthetic-only", http_client=transport)
        result = await AnthropicProvider(client).invoke(ProviderInput("Synthetic request"))
    assert count == 1
    assert result.provider == "anthropic"
    assert result.model == "claude-haiku-4-5-20251001"
    assert result.output == "Synthetic answer."
    assert result.finish_reason is FinishReason.STOP
    assert (result.usage.input_tokens, result.usage.output_tokens) == (7, 4)


@pytest.mark.parametrize(
    "variation,expected",
    [
        ("missing_usage", FinishReason.STOP),
        ("truncation", FinishReason.LENGTH),
        ("refusal", FinishReason.REFUSAL),
    ],
)
async def test_anthropic_normalizes_other_success_shapes(
    variation: str, expected: FinishReason
) -> None:
    payload = response_payload()
    if variation == "missing_usage":
        payload["usage"] = None
    else:
        payload["stop_reason"] = "max_tokens" if variation == "truncation" else "refusal"

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as transport:
        client = anthropic.AsyncAnthropic(api_key="synthetic-only", http_client=transport)
        result = await AnthropicProvider(client).invoke(ProviderInput("Synthetic request"))
    assert result.finish_reason is expected
    if variation == "missing_usage":
        assert result.usage.status == "unknown"
        assert result.usage.input_tokens is None


@pytest.mark.parametrize(
    "status,kind",
    [
        (400, FailureKind.INVALID_REQUEST),
        (401, FailureKind.CREDENTIAL),
        (403, FailureKind.CREDENTIAL),
        (429, FailureKind.RATE_LIMIT),
        (500, FailureKind.SERVER),
        (503, FailureKind.SERVER),
    ],
)
async def test_anthropic_classifies_http_faults_without_sdk_retries(
    status: int, kind: FailureKind
) -> None:
    count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        return httpx2.Response(
            status,
            json={
                "type": "error",
                "error": {"type": "api_error", "message": "synthetic secret-like detail"},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as transport:
        client = anthropic.AsyncAnthropic(api_key="synthetic-only", http_client=transport)
        with pytest.raises(ProviderFailure) as caught:
            await AnthropicProvider(client).invoke(ProviderInput("Synthetic request"))
    assert count == 1
    assert caught.value.kind is kind
    assert "secret-like" not in str(caught.value)


@pytest.mark.parametrize(
    "exception,kind",
    [
        (httpx2.ReadTimeout("synthetic timeout"), FailureKind.TIMEOUT),
        (httpx2.ConnectError("synthetic connection"), FailureKind.CONNECTION),
    ],
)
async def test_anthropic_classifies_transport_faults_without_sdk_retries(
    exception: Exception, kind: FailureKind
) -> None:
    count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        raise exception

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as transport:
        client = anthropic.AsyncAnthropic(api_key="synthetic-only", http_client=transport)
        with pytest.raises(ProviderFailure) as caught:
            await AnthropicProvider(client).invoke(ProviderInput("Synthetic request"))
    assert count == 1
    assert caught.value.kind is kind


async def test_anthropic_schema_is_prompted_and_invalid_json_stays_untrusted() -> None:
    payload = response_payload()
    payload["content"][0]["text"] = "{invalid JSON"

    def handler(request: httpx2.Request) -> httpx2.Response:
        wire = json.loads(request.content)
        assert "schema" in wire["system"]
        assert wire["max_tokens"] == 32
        return httpx2.Response(200, json=payload)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as transport:
        client = anthropic.AsyncAnthropic(api_key="synthetic-only", http_client=transport)
        result = await AnthropicProvider(client).invoke(
            ProviderInput("Synthetic request", schema={"type": "object"}, max_output_tokens=32)
        )
    assert result.output == "{invalid JSON"
