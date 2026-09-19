from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
type JSONSchema = dict[str, JSONValue]

if TYPE_CHECKING:
    from app.domain.routing import RegistrySnapshot


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    REFUSAL = "refusal"
    UNKNOWN = "unknown"


class FailureKind(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    CREDENTIAL = "credential"
    INVALID_REQUEST = "invalid_request"


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None
    output_tokens: int | None
    status: str = "observed"


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    model: str
    structured_output: bool
    context_limit: int
    output_limit: int


@dataclass(frozen=True)
class ProviderInput:
    text: str
    task_type: str = "generation"
    schema: JSONSchema | None = None
    temperature: float = 0
    max_output_tokens: int = 512


@dataclass(frozen=True)
class ProviderResult:
    output: str
    provider: str
    model: str
    finish_reason: FinishReason
    usage: TokenUsage


class ProviderFailure(Exception):
    def __init__(self, kind: FailureKind, usage: TokenUsage | None = None) -> None:
        # Deliberately omit provider wire messages and request content.
        super().__init__(kind.value)
        self.kind = kind
        self.usage = usage or TokenUsage(None, None, "unknown")


class Provider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def invoke(self, request: ProviderInput) -> ProviderResult: ...


@dataclass(frozen=True)
class Principal:
    tenant_id: UUID
    application_id: str
    credential_id: UUID
    role: str


@dataclass(frozen=True)
class ExecutionSnapshot:
    policy_id: UUID
    policy_version: str
    model_id: UUID
    pricing_id: UUID
    pricing_version: str
    input_price: Decimal
    output_price: Decimal
    provider: str = "mock"
    model: str = "mock-text-v1"
    routing_evidence: dict[str, JSONValue] | None = None


@dataclass(frozen=True)
class Dispatch:
    request_id: UUID
    attempt_id: UUID


@dataclass(frozen=True)
class RequestEvidence:
    request_id: UUID
    tenant_id: UUID
    application_id: str
    status: str
    policy_version: str
    error_code: str | None
    routing_evidence: dict[str, JSONValue] | None = None


class Store(Protocol):
    async def authenticate(self, key_hash: str) -> Principal | None: ...

    async def ready(self) -> bool: ...

    async def snapshot(self) -> ExecutionSnapshot: ...

    async def registry(self) -> "RegistrySnapshot": ...

    async def provider_enabled(self, provider: str) -> bool: ...

    async def schema(self, principal: Principal, name: str, version: str) -> JSONSchema | None: ...

    async def begin(
        self,
        principal: Principal,
        request_id: UUID,
        endpoint: str,
        input_hash: str,
        schema_hash: str | None,
        snapshot: ExecutionSnapshot,
    ) -> Dispatch: ...

    async def reject_routing(
        self,
        principal: Principal,
        request_id: UUID,
        endpoint: str,
        input_hash: str,
        schema_hash: str | None,
        policy_id: UUID,
        policy_version: str,
        routing_evidence: dict[str, JSONValue],
    ) -> None: ...

    async def finish(
        self,
        dispatch: Dispatch,
        status: str,
        finish_reason: FinishReason | None,
        usage: TokenUsage,
        cost: Decimal,
        error_code: str | None,
        error_class: str | None,
    ) -> None: ...

    async def evidence(self, principal: Principal, request_id: UUID) -> RequestEvidence | None: ...


class StateUnavailable(Exception):
    """Sanitized failure of correctness-critical storage."""
