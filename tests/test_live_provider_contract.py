"""One provider-neutral offline contract over mock and both pinned SDK adapters."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import anthropic
import httpx2
import openai
import pytest

from app.domain.models import (
    FailureKind,
    FinishReason,
    Provider,
    ProviderFailure,
    ProviderInput,
)
from app.providers.anthropic_adapter import AnthropicProvider
from app.providers.mock import MockProvider, MockStep
from app.providers.openai_adapter import OpenAIProvider

FIXTURES = Path(__file__).parent / "fixtures" / "providers"
ERROR_STATUS = {
    "rate_limit": 429,
    "server": 503,
    "credential": 401,
    "invalid_request": 400,
}


def synthetic_payload(provider: str, outcome: str) -> dict[str, Any]:
    payload = cast(
        dict[str, Any],
        json.loads((FIXTURES / provider / "success.json").read_text()),
    )
    if outcome == "missing_usage":
        payload["usage"] = None
    elif outcome == "refusal":
        if provider == "openai":
            payload["output"][0]["content"] = [{"type": "refusal", "refusal": "Synthetic refusal"}]
        else:
            payload["stop_reason"] = "refusal"
    elif outcome == "truncation":
        if provider == "openai":
            payload["status"] = "incomplete"
            payload["incomplete_details"] = {"reason": "max_output_tokens"}
        else:
            payload["stop_reason"] = "max_tokens"
    elif outcome == "malformed":
        if provider == "openai":
            payload["output"][0]["content"][0]["text"] = "{invalid JSON"
        else:
            payload["content"][0]["text"] = "{invalid JSON"
    return payload


@asynccontextmanager
async def offline_provider(provider: str, outcome: str) -> AsyncIterator[Provider]:
    if provider == "mock":
        yield MockProvider([MockStep(outcome)])
        return

    def handler(request: httpx2.Request) -> httpx2.Response:
        if outcome == "timeout":
            raise httpx2.ReadTimeout("Synthetic timeout")
        if outcome == "connection":
            raise httpx2.ConnectError("Synthetic connection failure")
        if outcome in ERROR_STATUS:
            if provider == "openai":
                error: dict[str, Any] = {"error": {"type": "error", "message": "Synthetic only"}}
            else:
                error = {
                    "type": "error",
                    "error": {"type": "api_error", "message": "Synthetic only"},
                }
            return httpx2.Response(ERROR_STATUS[outcome], json=error)
        return httpx2.Response(200, json=synthetic_payload(provider, outcome))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as transport:
        if provider == "openai":
            client = openai.AsyncOpenAI(api_key="synthetic-only", http_client=transport)
            try:
                yield OpenAIProvider(client)
            finally:
                await client.close()
        else:
            client_a = anthropic.AsyncAnthropic(api_key="synthetic-only", http_client=transport)
            try:
                yield AnthropicProvider(client_a)
            finally:
                await client_a.close()


@pytest.mark.parametrize("provider", ["mock", "openai", "anthropic"])
@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("success", FinishReason.STOP),
        ("refusal", FinishReason.REFUSAL),
        ("truncation", FinishReason.LENGTH),
        ("missing_usage", FinishReason.STOP),
        ("malformed", FinishReason.STOP),
    ],
)
async def test_common_result_contract(provider: str, outcome: str, expected: FinishReason) -> None:
    async with offline_provider(provider, outcome) as adapter:
        result = await adapter.invoke(ProviderInput("Synthetic request"))
        assert result.provider == adapter.capabilities.provider
        assert result.model == adapter.capabilities.model
        assert result.finish_reason is expected
        assert isinstance(result.output, str)
        assert isinstance(result.usage.status, str)
        if outcome == "missing_usage":
            assert result.usage.input_tokens is None
            assert result.usage.output_tokens is None
            assert result.usage.status == "unknown"
        else:
            assert result.usage.input_tokens is not None
            assert result.usage.output_tokens is not None
        if outcome == "malformed":
            assert result.output.startswith("{invalid")


@pytest.mark.parametrize("provider", ["mock", "openai", "anthropic"])
@pytest.mark.parametrize("kind", list(FailureKind))
async def test_common_failure_contract(provider: str, kind: FailureKind) -> None:
    async with offline_provider(provider, kind.value) as adapter:
        with pytest.raises(ProviderFailure) as caught:
            await adapter.invoke(ProviderInput("Synthetic request"))
    assert caught.value.kind is kind
    assert caught.value.usage.status == "unknown"
    assert "Synthetic request" not in str(caught.value)
