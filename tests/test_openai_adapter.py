import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx2
import openai
import pytest

from app.domain.models import FailureKind, FinishReason, ProviderFailure, ProviderInput
from app.providers.openai_adapter import OpenAIProvider

FIXTURE = Path(__file__).parent / "fixtures" / "providers" / "openai" / "success.json"


def response_payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text()))


@pytest.fixture
async def adapter() -> AsyncIterator[OpenAIProvider]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/responses"
        wire = json.loads(request.content)
        assert wire["store"] is False
        assert wire["stream"] is False
        assert wire["tools"] == []
        assert wire["tool_choice"] == "none"
        return httpx2.Response(200, json=response_payload())

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as http_client:
        client = openai.AsyncOpenAI(api_key="synthetic-only", http_client=http_client)
        try:
            yield OpenAIProvider(client)
        finally:
            await client.close()


async def test_openai_normalizes_success(adapter: OpenAIProvider) -> None:
    result = await adapter.invoke(ProviderInput("Synthetic request"))
    assert result.provider == "openai"
    assert result.model == "gpt-5.6-luna"
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
async def test_openai_normalizes_other_success_shapes(
    variation: str, expected: FinishReason
) -> None:
    payload = response_payload()
    if variation == "missing_usage":
        payload["usage"] = None
    elif variation == "truncation":
        payload["status"] = "incomplete"
        payload["incomplete_details"] = {"reason": "max_output_tokens"}
    else:
        payload["output"][0]["content"] = [{"type": "refusal", "refusal": "Synthetic refusal"}]
    count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        return httpx2.Response(200, json=payload)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as transport:
        client = openai.AsyncOpenAI(api_key="synthetic-only", http_client=transport)
        result = await OpenAIProvider(client).invoke(ProviderInput("Synthetic request"))
    assert count == 1
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
async def test_openai_classifies_http_faults_without_sdk_retries(
    status: int, kind: FailureKind
) -> None:
    count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        return httpx2.Response(
            status,
            json={"error": {"message": "synthetic secret-like wire detail", "type": "error"}},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as transport:
        client = openai.AsyncOpenAI(api_key="synthetic-only", http_client=transport)
        with pytest.raises(ProviderFailure) as caught:
            await OpenAIProvider(client).invoke(ProviderInput("Synthetic request"))
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
async def test_openai_classifies_transport_faults_without_sdk_retries(
    exception: Exception, kind: FailureKind
) -> None:
    count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        raise exception

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as transport:
        client = openai.AsyncOpenAI(api_key="synthetic-only", http_client=transport)
        with pytest.raises(ProviderFailure) as caught:
            await OpenAIProvider(client).invoke(ProviderInput("Synthetic request"))
    assert count == 1
    assert caught.value.kind is kind


async def test_openai_extraction_payload_is_bounded_and_untrusted() -> None:
    payload = response_payload()
    payload["output"][0]["content"][0]["text"] = "{invalid JSON"

    def handler(request: httpx2.Request) -> httpx2.Response:
        wire = json.loads(request.content)
        assert "schema" in wire["instructions"]
        assert wire["max_output_tokens"] == 32
        assert wire["temperature"] == 0
        return httpx2.Response(200, json=payload)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as transport:
        client = openai.AsyncOpenAI(api_key="synthetic-only", http_client=transport)
        result = await OpenAIProvider(client).invoke(
            ProviderInput("Synthetic request", schema={"type": "object"}, max_output_tokens=32)
        )
    assert result.output == "{invalid JSON"
    # Local schema validation in GatewayService decides whether it can succeed.
