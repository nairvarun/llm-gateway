import json
from pathlib import Path

from anthropic.types import Message
from openai.types.responses import Response

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def test_pinned_openai_synthetic_fixture_parses() -> None:
    payload = json.loads((FIXTURES / "openai" / "success.json").read_text())
    response = Response.model_validate(payload)
    assert response.model == "gpt-5.6-luna"
    assert response.output_text == "Synthetic answer."
    assert response.usage is not None and response.usage.input_tokens == 7


def test_pinned_anthropic_synthetic_fixture_parses() -> None:
    payload = json.loads((FIXTURES / "anthropic" / "success.json").read_text())
    response = Message.model_validate(payload)
    assert response.model == "claude-haiku-4-5-20251001"
    assert response.content[0].type == "text"
    assert response.content[0].text == "Synthetic answer."
    assert response.usage.input_tokens == 7
