from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.models import (
    Dispatch,
    ExecutionSnapshot,
    FinishReason,
    JSONSchema,
    JSONValue,
    Principal,
    RequestEvidence,
    StateUnavailable,
    TokenUsage,
)
from app.domain.routing import (
    ModelPayload,
    ModelSnapshot,
    PolicyPayload,
    PricingPayload,
    RegistrySnapshot,
)
from app.persistence.bootstrap import DEMO_SCHEMA
from app.security.auth import hash_api_key, new_api_key


class MemoryStore:
    """Explicit unit-test double; never used by local/runtime application code."""

    def __init__(self) -> None:
        self.key = new_api_key()
        self.principal = Principal(uuid4(), "test-app", uuid4(), "tenant")
        self.available = True
        self.fail_begin = False
        self.fail_finish = False
        self.records: dict[UUID, RequestEvidence] = {}
        self.usages: dict[UUID, TokenUsage] = {}
        model = ModelSnapshot(
            uuid4(),
            "v2",
            ModelPayload(
                provider="mock",
                model="mock-text-v1",
                pricing_version="v2",
                tasks=frozenset({"generation", "extraction", "classification", "summarization"}),
                structured_output=True,
                context_limit=120_000,
                output_limit=16_384,
                token_bound="mock_utf8_bytes",
                quality_score=Decimal("0.5"),
                latency_score=Decimal("0.5"),
            ),
            uuid4(),
            "v2",
            PricingPayload(input_per_million=0, output_per_million=0),
        )
        self.routing = RegistrySnapshot(
            uuid4(),
            "mock-policy",
            "v2",
            PolicyPayload.model_validate(
                {
                    "candidates": [{"name": "mock-text-v1", "version": "v2", "order": 0}],
                    "weights": {
                        "quality": "1",
                        "affordability": "1",
                        "latency": "1",
                        "health": "1",
                    },
                    "affordability_reference_usd": "1",
                    "minimum_deadline_ms": 0,
                    "attempt_limit": 1,
                }
            ),
            (model,),
            frozenset(),
        )

    async def authenticate(self, key_hash: str) -> Principal | None:
        if not self.available:
            raise StateUnavailable()
        return self.principal if key_hash == hash_api_key(self.key) else None

    async def ready(self) -> bool:
        return self.available

    async def snapshot(self) -> ExecutionSnapshot:
        model = self.routing.models[0]
        return ExecutionSnapshot(
            self.routing.policy_id,
            self.routing.policy_version,
            model.id,
            model.pricing_id,
            model.pricing_version,
            Decimal("0"),
            Decimal("0"),
        )

    async def registry(self) -> RegistrySnapshot:
        if not self.available:
            raise StateUnavailable()
        return self.routing

    async def provider_enabled(self, provider: str) -> bool:
        if not self.available:
            raise StateUnavailable()
        return provider not in self.routing.disabled_providers

    async def schema(self, principal: Principal, name: str, version: str) -> JSONSchema | None:
        return (
            DEMO_SCHEMA
            if principal.tenant_id == self.principal.tenant_id
            and (name, version) == ("demo-count", "v1")
            else None
        )

    async def begin(
        self,
        principal: Principal,
        request_id: UUID,
        endpoint: str,
        input_hash: str,
        schema_hash: str | None,
        snapshot: ExecutionSnapshot,
    ) -> Dispatch:
        if self.fail_begin:
            raise StateUnavailable()
        self.records[request_id] = RequestEvidence(
            request_id,
            principal.tenant_id,
            principal.application_id,
            "in_progress",
            snapshot.policy_version,
            None,
            snapshot.routing_evidence,
        )
        return Dispatch(request_id, uuid4())

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
    ) -> None:
        if self.fail_begin:
            raise StateUnavailable()
        self.records[request_id] = RequestEvidence(
            request_id,
            principal.tenant_id,
            principal.application_id,
            "failed",
            policy_version,
            "NO_ELIGIBLE_MODEL",
            routing_evidence,
        )

    async def finish(
        self,
        dispatch: Dispatch,
        status: str,
        finish_reason: FinishReason | None,
        usage: TokenUsage,
        cost: Decimal,
        error_code: str | None,
        error_class: str | None,
    ) -> None:
        if self.fail_finish:
            raise StateUnavailable()
        self.records[dispatch.request_id] = replace(
            self.records[dispatch.request_id],
            status=status,
            error_code=error_code,
        )
        self.usages[dispatch.request_id] = usage

    async def evidence(self, principal: Principal, request_id: UUID) -> RequestEvidence | None:
        record = self.records.get(request_id)
        return (
            record
            if record and (record.tenant_id == principal.tenant_id or principal.role == "operator")
            else None
        )
