from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator

from app.domain.models import FinishReason, JSONSchema

ShortString = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=100)]
MetadataKey = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]
MetadataValue = Annotated[str, StringConstraints(strict=True, max_length=256)]


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=100_000)]
    model_policy: ShortString = "mock"
    quality_tier: Literal["economy", "standard", "high"] = "standard"
    task_type: Literal["generation", "extraction", "classification", "summarization"] = "generation"
    latency_budget_ms: int = Field(default=30_000, ge=100, le=120_000, strict=True)
    max_cost_usd: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=10)
    temperature: float = Field(default=0, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=512, ge=1, le=16_384, strict=True)
    cache_mode: Literal["bypass", "read_only", "read_write"] = "bypass"
    metadata: dict[MetadataKey, MetadataValue] = Field(default_factory=dict, max_length=20)
    idempotency_key: (
        Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)] | None
    ) = None

    @field_validator("max_cost_usd", mode="before")
    @classmethod
    def decimal_string(cls, value: object) -> object:
        if value is not None and not isinstance(value, str):
            raise ValueError("USD amounts must be decimal strings")
        return value


class ExtractRequest(GenerateRequest):
    task_type: Literal["extraction"] = "extraction"
    json_schema: JSONSchema | None = None
    schema_name: ShortString | None = None
    schema_version: ShortString | None = None


class UsageResponse(BaseModel):
    input_tokens: int | None
    output_tokens: int | None
    status: str
    complete: bool
    attempt_count: int
    source_request_id: UUID | None = None


class ExecutionResponse(BaseModel):
    request_id: UUID
    provider: str
    model: str
    finish_reason: FinishReason
    usage: UsageResponse
    estimated_cost_usd: Decimal
    latency_ms: float
    cache_hit: bool = False
    idempotency_replayed: bool = False
    fallback_used: bool = False
    policy_version: str
    routing: dict[str, JsonValue] | None = None


class GenerateResponse(ExecutionResponse):
    output: str


class ExtractResponse(ExecutionResponse):
    output: JsonValue


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: UUID


class HealthResponse(BaseModel):
    status: Literal["live", "ready", "not_ready"]
    components: dict[str, str] = Field(default_factory=dict)
