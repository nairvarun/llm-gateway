"""Adapters against recorded provider responses, replayed with respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from gateway.config import ProviderCfg
from gateway.errors import BadRequest
from gateway.providers.anthropic import AnthropicProvider, from_anthropic, to_anthropic
from gateway.providers.base import ProviderError
from gateway.providers.openai import OpenAIProvider
from gateway.schemas import ChatRequest

OPENAI_STREAM = (
    b'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m",'
    b'"choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
    b'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m",'
    b'"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}\n\n'
    b"data: [DONE]\n\n"
)

ANTHROPIC_STREAM = b"""event: message_start
data: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":9}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: ping
data: {"type":"ping"}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hel"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"lo"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"output_tokens":2}}

event: message_stop
data: {"type":"message_stop"}

"""


def req(**kw) -> ChatRequest:
    kw.setdefault("model", "alias")
    kw.setdefault("messages", [{"role": "user", "content": "hi"}])
    return ChatRequest(**kw)


def openai() -> OpenAIProvider:
    return OpenAIProvider(ProviderCfg(base_url="https://oa.test/v1", api_key_env="X"), "sk", 1)


def anthropic() -> AnthropicProvider:
    return AnthropicProvider(ProviderCfg(base_url="https://an.test", api_key_env="X"), "ak", 1)


@respx.mock
async def test_openai_stream_parses_and_injects_usage():
    route = respx.post("https://oa.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=OPENAI_STREAM)
    )
    chunks = [c async for c in openai().stream(req(stream=True), "gpt-x")]
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer sk"
    body = json.loads(sent.content)
    assert body["model"] == "gpt-x" and body["stream_options"] == {"include_usage": True}
    assert chunks[0].choices[0]["delta"]["content"] == "Hi"
    assert chunks[1].usage["completion_tokens"] == 1


@respx.mock
async def test_openai_truncated_stream_is_protocol_error():
    respx.post("https://oa.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=OPENAI_STREAM.replace(b"data: [DONE]\n\n", b""))
    )
    with pytest.raises(ProviderError) as e:
        [c async for c in openai().stream(req(stream=True), "gpt-x")]
    assert e.value.kind == "protocol"


@respx.mock
async def test_openai_http_error_carries_status_and_retry_after():
    respx.post("https://oa.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            429, json={"error": {"message": "slow down"}}, headers={"retry-after": "2"}
        )
    )
    with pytest.raises(ProviderError) as e:
        await openai().complete(req(), "gpt-x")
    assert (e.value.kind, e.value.status, e.value.retry_after) == ("http", 429, 2.0)
    assert e.value.message == "slow down"


@respx.mock
async def test_connect_error_is_classified():
    respx.post("https://oa.test/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("refused")
    )
    with pytest.raises(ProviderError) as e:
        await openai().complete(req(), "gpt-x")
    assert e.value.kind == "connect"


@respx.mock
async def test_anthropic_stream_translation():
    route = respx.post("https://an.test/v1/messages").mock(
        return_value=httpx.Response(200, content=ANTHROPIC_STREAM)
    )
    chunks = [c async for c in anthropic().stream(req(stream=True), "claude-x")]
    sent = route.calls[0].request
    assert sent.headers["x-api-key"] == "ak"
    assert sent.headers["anthropic-version"] == "2023-06-01"
    deltas = [c.choices[0]["delta"] for c in chunks if c.choices]
    assert deltas[0]["role"] == "assistant"
    assert "".join(d.get("content", "") for d in deltas) == "Hello"
    assert chunks[-2].choices[0]["finish_reason"] == "length"
    assert chunks[-1].choices == []
    assert chunks[-1].usage == {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11}
    assert all(c.id == "msg_1" for c in chunks)


@respx.mock
async def test_anthropic_error_event_is_529_when_overloaded():
    body = (
        b'event: message_start\ndata: {"type":"message_start","message":{"id":"m"}}\n\n'
        b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",'
        b'"message":"Overloaded"}}\n\n'
    )
    respx.post("https://an.test/v1/messages").mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(ProviderError) as e:
        [c async for c in anthropic().stream(req(stream=True), "claude-x")]
    assert (e.value.kind, e.value.status) == ("http", 529)


def test_to_anthropic_request_translation():
    r = req(
        messages=[
            {"role": "system", "content": "rule one"},
            {"role": "developer", "content": [{"type": "text", "text": "rule two"}]},
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": "hello"},
        ],
        max_completion_tokens=50,
        temperature=0.4,
        top_p=0.9,
        stop=["a", "b"],
        user="u1",
    )
    body = to_anthropic(r, "claude-x", 1024, stream=False)
    assert body["system"] == "rule one\n\nrule two"
    assert body["messages"][0] == {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    assert body["max_tokens"] == 50
    assert (body["temperature"], body["top_p"]) == (0.4, 0.9)
    assert body["stop_sequences"] == ["a", "b"]
    assert body["metadata"] == {"user_id": "u1"}


def test_to_anthropic_defaults_and_refusals():
    body = to_anthropic(req(temperature=2.0), "c", 777, stream=True)
    assert body["max_tokens"] == 777 and body["temperature"] == 1.0
    with pytest.raises(BadRequest):
        to_anthropic(
            req(messages=[{"role": "user", "content": [{"type": "image_url"}]}]), "c", 1, False
        )
    with pytest.raises(BadRequest):
        to_anthropic(req(messages=[{"role": "tool", "content": "x"}]), "c", 1, False)


def test_from_anthropic_response():
    resp = from_anthropic(
        {
            "id": "msg_9",
            "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
            "stop_reason": "stop_sequence",
            "usage": {"input_tokens": 4, "output_tokens": 2},
        },
        "claude-x",
    )
    assert resp.choices[0]["message"]["content"] == "ab"
    assert resp.choices[0]["finish_reason"] == "stop"
    assert resp.usage["total_tokens"] == 6
