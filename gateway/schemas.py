"""OpenAI-shaped request and response models.

Every model allows extra fields, so anything the gateway does not know about passes through.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ChatRequest(_Open):
    model: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = None
    max_tokens: int | None = Field(None, gt=0)
    max_completion_tokens: int | None = Field(None, gt=0)
    stop: str | list[str] | None = None
    tools: list[dict[str, Any]] | None = None
    user: str | None = None

    def payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class ChatResponse(_Open):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[dict[str, Any]]
    usage: dict[str, Any] | None = None


class ChatChunk(_Open):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] | None = None
