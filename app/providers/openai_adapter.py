"""OpenAI Responses adapter. Not wired for live dispatch before budget gates."""

import json

import openai

from app.domain.models import (
    FailureKind,
    FinishReason,
    ProviderCapabilities,
    ProviderFailure,
    ProviderInput,
    ProviderResult,
    TokenUsage,
)


class OpenAIProvider:
    def __init__(self, client: openai.AsyncOpenAI, model: str = "gpt-5.6-luna") -> None:
        self.client = client
        self.model = model

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("openai", self.model, True, 1_050_000, 128_000)

    async def invoke(self, request: ProviderInput) -> ProviderResult:
        instructions = None
        if request.schema is not None:
            # The gateway still parses and validates the response locally. The
            # supported local dialect exceeds vendor native-schema subsets.
            schema = json.dumps(request.schema, sort_keys=True, separators=(",", ":"))
            instructions = (
                "Return only JSON matching this schema. Do not include Markdown or commentary. "
                + schema
            )
        try:
            # SDK defaults retry transient failures twice. Gateway ownership of
            # every attempt requires one SDK request per invoke, even when the
            # caller injected a client with different defaults.
            result = await self.client.with_options(max_retries=0).responses.create(
                model=self.model,
                input=request.text,
                instructions=instructions,
                max_output_tokens=request.max_output_tokens,
                temperature=request.temperature,
                reasoning={"effort": "none"},
                tools=[],
                tool_choice="none",
                store=False,
                stream=False,
            )
        except openai.APITimeoutError as error:
            raise ProviderFailure(FailureKind.TIMEOUT) from error
        except openai.APIConnectionError as error:
            raise ProviderFailure(FailureKind.CONNECTION) from error
        except openai.AuthenticationError as error:
            raise ProviderFailure(FailureKind.CREDENTIAL) from error
        except openai.PermissionDeniedError as error:
            raise ProviderFailure(FailureKind.CREDENTIAL) from error
        except openai.RateLimitError as error:
            raise ProviderFailure(FailureKind.RATE_LIMIT) from error
        except openai.BadRequestError as error:
            raise ProviderFailure(FailureKind.INVALID_REQUEST) from error
        except openai.APIStatusError as error:
            kind = FailureKind.SERVER if error.status_code >= 500 else FailureKind.INVALID_REQUEST
            raise ProviderFailure(kind) from error
        except openai.APIError as error:
            raise ProviderFailure(FailureKind.SERVER) from error
        # SDK/transport errors not covered by its public API hierarchy do not
        # expose raw messages through the gateway error contract.
        usage = (
            TokenUsage(result.usage.input_tokens, result.usage.output_tokens)
            if result.usage is not None
            else TokenUsage(None, None, "unknown")
        )
        if result.status in {"failed", "cancelled"}:
            raise ProviderFailure(FailureKind.SERVER, usage)
        if result.status == "incomplete":
            finish = FinishReason.LENGTH
        elif any(
            item.type == "message" and any(block.type == "refusal" for block in item.content)
            for item in result.output
        ):
            finish = FinishReason.REFUSAL
        elif result.status == "completed":
            finish = FinishReason.STOP
        else:
            finish = FinishReason.UNKNOWN
        return ProviderResult(result.output_text, "openai", result.model, finish, usage)
