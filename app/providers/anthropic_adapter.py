"""Anthropic Messages adapter. Not wired for live dispatch before budget gates."""

import json

import anthropic

from app.domain.models import (
    FailureKind,
    FinishReason,
    ProviderCapabilities,
    ProviderFailure,
    ProviderInput,
    ProviderResult,
    TokenUsage,
)


class AnthropicProvider:
    def __init__(
        self, client: anthropic.AsyncAnthropic, model: str = "claude-haiku-4-5-20251001"
    ) -> None:
        self.client = client
        self.model = model

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("anthropic", self.model, True, 200_000, 64_000)

    async def invoke(self, request: ProviderInput) -> ProviderResult:
        system = None
        if request.schema is not None:
            schema = json.dumps(request.schema, sort_keys=True, separators=(",", ":"))
            system = (
                "Return only JSON matching this schema. Do not include Markdown or commentary. "
                + schema
            )
        try:
            result = await self.client.with_options(max_retries=0).messages.create(
                model=self.model,
                messages=[{"role": "user", "content": request.text}],
                max_tokens=request.max_output_tokens,
                system=system if system is not None else anthropic.omit,
                tools=[],
                stream=False,
                # Haiku 4.5 accepts temperature, but this pinned SDK exposes it
                # through extra_body rather than a named create parameter.
                extra_body={"temperature": request.temperature},
            )
        except anthropic.APITimeoutError as error:
            raise ProviderFailure(FailureKind.TIMEOUT) from error
        except anthropic.APIConnectionError as error:
            raise ProviderFailure(FailureKind.CONNECTION) from error
        except anthropic.AuthenticationError as error:
            raise ProviderFailure(FailureKind.CREDENTIAL) from error
        except anthropic.PermissionDeniedError as error:
            raise ProviderFailure(FailureKind.CREDENTIAL) from error
        except anthropic.RateLimitError as error:
            raise ProviderFailure(FailureKind.RATE_LIMIT) from error
        except anthropic.BadRequestError as error:
            raise ProviderFailure(FailureKind.INVALID_REQUEST) from error
        except anthropic.APIStatusError as error:
            kind = FailureKind.SERVER if error.status_code >= 500 else FailureKind.INVALID_REQUEST
            raise ProviderFailure(kind) from error
        except anthropic.APIError as error:
            raise ProviderFailure(FailureKind.SERVER) from error
        usage_source = getattr(result, "usage", None)
        usage = (
            TokenUsage(
                usage_source.input_tokens
                + (usage_source.cache_creation_input_tokens or 0)
                + (usage_source.cache_read_input_tokens or 0),
                usage_source.output_tokens,
            )
            if usage_source is not None
            else TokenUsage(None, None, "unknown")
        )
        stop_reason: str | None = result.stop_reason
        if stop_reason == "max_tokens":
            finish = FinishReason.LENGTH
        elif stop_reason == "refusal":
            finish = FinishReason.REFUSAL
        elif stop_reason in {"end_turn", "stop_sequence"}:
            finish = FinishReason.STOP
        else:
            finish = FinishReason.UNKNOWN
        output = "".join(
            block.text for block in result.content if isinstance(block, anthropic.types.TextBlock)
        )
        return ProviderResult(output, "anthropic", result.model, finish, usage)
