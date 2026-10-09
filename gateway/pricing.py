"""Cost from the pricing table, and a rough token estimate for streams that were cut short."""

from __future__ import annotations

import math
from typing import Any

from gateway.config import Price
from gateway.context import Usage


def cost_usd(price: Price, usage: Usage) -> float:
    total = usage.input_tokens * price.input + usage.output_tokens * price.output
    return round(total / 1_000_000, 6)


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """About 4 characters per token, plus a little per message for role overhead."""
    chars = sum(len(text_of(m.get("content"))) for m in messages)
    return math.ceil(chars / 4) + 4 * len(messages)
