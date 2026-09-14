import asyncio
import hashlib
import hmac
from decimal import Decimal
from time import monotonic
from uuid import UUID

from app.api.schema_validation import validate_output, validate_schema
from app.api.schemas import (
    ExtractRequest,
    ExtractResponse,
    GenerateRequest,
    GenerateResponse,
    UsageResponse,
)
from app.config import Settings
from app.domain.errors import GatewayError
from app.domain.models import (
    FailureKind,
    FinishReason,
    JSONSchema,
    JSONValue,
    Principal,
    Provider,
    ProviderFailure,
    ProviderInput,
    StateUnavailable,
    Store,
    TokenUsage,
)


class GatewayService:
    def __init__(self, settings: Settings, store: Store, provider: Provider) -> None:
        self.settings, self.store, self.provider = settings, store, provider

    async def execute(
        self,
        request: GenerateRequest,
        principal: Principal,
        request_id: UUID,
        started: float,
    ) -> GenerateResponse | ExtractResponse:
        if len(request.input) > self.settings.input_limit_chars:
            raise GatewayError("INVALID_REQUEST", "Input exceeds the configured limit.", 422)
        # Never silently accept an unimplemented safety-affecting feature.
        if request.cache_mode != "bypass" or request.idempotency_key is not None:
            raise GatewayError(
                "INVALID_REQUEST", "Cache/idempotency execution is not available yet.", 422
            )
        if request.model_policy not in {"mock", "mock-v1", "mock@v1"}:
            raise GatewayError(
                "NO_ELIGIBLE_MODEL", "Only the offline mock policy is available.", 503
            )
        schema: JSONSchema | None = None
        schema_hash: str | None = None
        if isinstance(request, ExtractRequest):
            named = request.schema_name is not None or request.schema_version is not None
            if (request.json_schema is not None) == named:
                raise GatewayError(
                    "INVALID_SCHEMA", "Provide exactly one inline or named schema.", 422
                )
            if named:
                if request.schema_name is None or request.schema_version is None:
                    raise GatewayError(
                        "INVALID_SCHEMA", "A schema name requires an explicit version.", 422
                    )
                schema = await self.store.schema(
                    principal, request.schema_name, request.schema_version
                )
                if schema is None:
                    raise GatewayError(
                        "INVALID_SCHEMA", "Schema is unavailable in this tenant.", 422
                    )
            else:
                schema = request.json_schema
            assert schema is not None
            schema_hash = validate_schema(
                schema,
                max_bytes=self.settings.schema_limit_bytes,
                max_depth=self.settings.schema_max_depth,
            )
        elif request.task_type == "extraction":
            raise GatewayError("INVALID_REQUEST", "Use /v1/extract for structured extraction.", 422)
        snapshot = await self.store.snapshot()
        input_hash = hmac.new(
            self.settings.input_hash_key.get_secret_value().encode(),
            request.input.encode(),
            hashlib.sha256,
        ).hexdigest()
        dispatch = await self.store.begin(
            principal,
            request_id,
            "/v1/extract" if schema is not None else "/v1/generate",
            input_hash,
            schema_hash,
            snapshot,
        )
        usage = TokenUsage(None, None, "unknown")
        finish_reason: FinishReason | None = None
        error_class: str | None = None
        failure: GatewayError | None = None
        output: JSONValue = None
        result_provider, result_model = "mock", "mock-text-v1"
        try:
            remaining = request.latency_budget_ms / 1000 - (monotonic() - started)
            if remaining <= 0:
                raise TimeoutError()
            result = await asyncio.wait_for(
                self.provider.invoke(
                    ProviderInput(
                        request.input,
                        request.task_type,
                        schema,
                        request.temperature,
                        request.max_output_tokens,
                    )
                ),
                remaining,
            )
            usage, finish_reason = result.usage, result.finish_reason
            result_provider, result_model = result.provider, result.model
            output = result.output
            if schema is not None:
                if finish_reason in {FinishReason.LENGTH, FinishReason.REFUSAL}:
                    raise GatewayError(
                        "OUTPUT_VALIDATION_FAILED", "Extraction was truncated or refused.", 502
                    )
                output = validate_output(result.output, schema)
        except ProviderFailure as error:
            usage, error_class = error.usage, error.kind.value
            if error.kind == FailureKind.INVALID_REQUEST:
                failure = GatewayError(
                    "UPSTREAM_REQUEST_REJECTED", "Provider rejected the request.", 502
                )
            else:
                failure = GatewayError(
                    "PROVIDER_UNAVAILABLE", "The mock provider could not complete the request.", 503
                )
        except TimeoutError:
            error_class = "timeout"
            failure = GatewayError("DEADLINE_EXCEEDED", "Request deadline expired.", 504)
        except GatewayError as error:
            error_class, failure = "output_validation", error
        except asyncio.CancelledError:
            await asyncio.shield(
                self.store.finish(
                    dispatch,
                    "uncertain",
                    finish_reason,
                    usage,
                    Decimal("0"),
                    "EXECUTION_UNCERTAIN",
                    "cancelled",
                )
            )
            raise
        try:
            await self.store.finish(
                dispatch,
                "failed" if failure else "completed",
                finish_reason,
                usage,
                Decimal("0"),
                failure.code if failure else None,
                error_class,
            )
        except StateUnavailable as error:
            raise GatewayError(
                "DEPENDENCY_UNAVAILABLE",
                "Execution outcome could not be recorded; execution may have occurred.",
                503,
            ) from error
        if failure:
            raise failure
        common = {
            "request_id": request_id,
            "provider": result_provider,
            "model": result_model,
            "finish_reason": finish_reason,
            "usage": UsageResponse(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                status=usage.status,
                complete=usage.input_tokens is not None and usage.output_tokens is not None,
                attempt_count=1,
            ),
            "estimated_cost_usd": Decimal("0"),
            "latency_ms": (monotonic() - started) * 1000,
            "policy_version": snapshot.policy_version,
        }
        if schema is not None:
            return ExtractResponse.model_validate({**common, "output": output})
        return GenerateResponse.model_validate({**common, "output": output})
