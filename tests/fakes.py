import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.models import (
    BudgetExceeded,
    Dispatch,
    ExecutionSnapshot,
    FinishReason,
    IdempotencyClaim,
    JSONSchema,
    JSONValue,
    Principal,
    RequestEvidence,
    SpendBucketView,
    SpendSummary,
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
        self.attempts: dict[UUID, list[Dispatch]] = {}
        self.attempt_outcomes: dict[UUID, tuple[str, str | None, Decimal]] = {}
        self.request_ceilings: dict[UUID, Decimal] = {}
        self.daily_budget_usd = Decimal("10")
        self.monthly_budget_usd = Decimal("100")
        self.cache_allowed = True  # The test tenant uses only synthetic fixtures.
        self.cache_namespace_generation = 1
        self.cache_key_generations: dict[str, int] = {}
        self.reserved_liability: dict[UUID, Decimal] = {}
        self.keyed: dict[
            tuple[UUID, str, str], tuple[str, UUID, str, bytes | None, datetime, datetime]
        ] = {}
        self.keyed_ingress: dict[UUID, tuple[UUID, str]] = {}
        self.keyed_lock = asyncio.Lock()
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

    async def claim_idempotency(
        self,
        principal: Principal,
        endpoint: str,
        key_hash: str,
        fingerprint: str,
        ingress_id: UUID,
        expires_at: datetime,
        owner_expires_at: datetime,
    ) -> IdempotencyClaim:
        if not self.available:
            raise StateUnavailable()
        identity = (principal.tenant_id, endpoint, key_hash)
        async with self.keyed_lock:
            existing = self.keyed.get(identity)
            now = datetime.now(UTC)
            if existing is None or existing[4] <= now:
                self.keyed[identity] = (
                    fingerprint,
                    ingress_id,
                    "in_progress",
                    None,
                    expires_at,
                    owner_expires_at,
                )
                return IdempotencyClaim("owned", ingress_id)
            prior_fingerprint, original, status, encrypted, _, owner_expiry = existing
            if fingerprint != prior_fingerprint:
                outcome = "conflict"
            elif status == "in_progress" and owner_expiry <= now:
                outcome = "uncertain"
                self.keyed[identity] = (
                    prior_fingerprint,
                    original,
                    outcome,
                    encrypted,
                    existing[4],
                    owner_expiry,
                )
                if original in self.records and self.records[original].status == "in_progress":
                    self.records[original] = replace(
                        self.records[original], status="uncertain", error_code="EXECUTION_UNCERTAIN"
                    )
            elif status == "in_progress":
                outcome = "in_progress"
            elif status in {"completed", "failed"} and encrypted is not None:
                outcome = "replay"
            else:
                outcome = "uncertain"
            self.keyed_ingress[ingress_id] = (original, outcome)
            return IdempotencyClaim(outcome, original, encrypted)

    async def seal_idempotency(
        self,
        tenant_id: UUID,
        endpoint: str,
        key_hash: str,
        original_request_id: UUID,
        status: str,
        encrypted_result: bytes,
    ) -> None:
        if self.fail_finish:
            raise StateUnavailable()
        identity = (tenant_id, endpoint, key_hash)
        async with self.keyed_lock:
            fingerprint, original, previous, _, expires, owner_expires = self.keyed[identity]
            if original != original_request_id or previous != "in_progress":
                raise StateUnavailable()
            if self.records[original].status != status:
                raise StateUnavailable()
            self.keyed[identity] = (
                fingerprint,
                original,
                status,
                encrypted_result,
                expires,
                owner_expires,
            )

    async def mark_idempotency_uncertain(self, original_request_id: UUID) -> None:
        async with self.keyed_lock:
            for identity, row in self.keyed.items():
                if row[1] == original_request_id and row[2] == "in_progress":
                    self.keyed[identity] = (*row[:2], "uncertain", *row[3:])

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

    async def cache_approved(self, principal: Principal) -> bool:
        if not self.available:
            raise StateUnavailable()
        return self.cache_allowed and principal.tenant_id == self.principal.tenant_id

    async def spend_summary(self, principal: Principal, tenant_id: UUID) -> SpendSummary:
        from app.domain.errors import GatewayError

        if principal.role != "operator" and tenant_id != principal.tenant_id:
            raise GatewayError("FORBIDDEN", "Spend summary is unavailable in this scope.", 403)
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        month = today.replace(day=1)
        held = sum(self.reserved_liability.values(), Decimal("0"))
        return SpendSummary(
            tenant_id,
            SpendBucketView(today, self.daily_budget_usd, Decimal("0"), held),
            SpendBucketView(month, self.monthly_budget_usd, Decimal("0"), held),
        )

    async def cache_generations(self, principal: Principal, key_hash: str) -> tuple[int, int]:
        if not self.available:
            raise StateUnavailable()
        return self.cache_namespace_generation, self.cache_key_generations.get(key_hash, 1)

    async def begin(
        self,
        principal: Principal,
        request_id: UUID,
        endpoint: str,
        input_hash: str,
        schema_hash: str | None,
        snapshot: ExecutionSnapshot,
        max_cost_usd: Decimal = Decimal("1"),
    ) -> Dispatch:
        if self.fail_begin:
            raise StateUnavailable()
        if snapshot.estimated_max_cost_usd > min(
            max_cost_usd, self.daily_budget_usd, self.monthly_budget_usd
        ):
            raise BudgetExceeded()
        self.records[request_id] = RequestEvidence(
            request_id,
            principal.tenant_id,
            principal.application_id,
            "in_progress",
            snapshot.policy_version,
            None,
            snapshot.routing_evidence,
        )
        dispatch = Dispatch(request_id, uuid4())
        self.attempts[request_id] = [dispatch]
        self.request_ceilings[request_id] = max_cost_usd
        self.reserved_liability[dispatch.attempt_id] = snapshot.estimated_max_cost_usd
        return dispatch

    async def add_attempt(
        self, request_id: UUID, number: int, snapshot: ExecutionSnapshot
    ) -> Dispatch:
        if self.fail_begin:
            raise StateUnavailable()
        if number != len(self.attempts[request_id]) + 1:
            raise ValueError("Attempt number is not sequential")
        spent = sum(self.reserved_liability[item.attempt_id] for item in self.attempts[request_id])
        if spent + snapshot.estimated_max_cost_usd > self.request_ceilings[request_id]:
            raise BudgetExceeded()
        dispatch = Dispatch(request_id, uuid4())
        self.attempts[request_id].append(dispatch)
        self.reserved_liability[dispatch.attempt_id] = snapshot.estimated_max_cost_usd
        return dispatch

    async def settle_attempt(
        self,
        dispatch: Dispatch,
        status: str,
        finish_reason: FinishReason | None,
        usage: TokenUsage,
        cost: Decimal,
        error_class: str | None,
    ) -> None:
        if self.fail_finish:
            raise StateUnavailable()
        self.usages[dispatch.attempt_id] = usage
        self.attempt_outcomes[dispatch.attempt_id] = (status, error_class, cost)
        if status == "not_dispatched":
            self.reserved_liability[dispatch.attempt_id] = Decimal("0")
        elif usage.input_tokens is not None and usage.output_tokens is not None:
            self.reserved_liability[dispatch.attempt_id] = cost

    async def complete_request(self, request_id: UUID, status: str, error_code: str | None) -> None:
        if self.fail_finish:
            raise StateUnavailable()
        self.records[request_id] = replace(
            self.records[request_id], status=status, error_code=error_code
        )

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
        error_code: str = "NO_ELIGIBLE_MODEL",
    ) -> None:
        if self.fail_begin:
            raise StateUnavailable()
        self.records[request_id] = RequestEvidence(
            request_id,
            principal.tenant_id,
            principal.application_id,
            "failed",
            policy_version,
            error_code,
            routing_evidence,
        )

    async def record_cache_hit(
        self,
        principal: Principal,
        request_id: UUID,
        endpoint: str,
        input_hash: str,
        schema_hash: str | None,
        snapshot: ExecutionSnapshot,
        source_request_id: UUID,
        max_cost_usd: Decimal,
    ) -> None:
        if self.fail_begin:
            raise StateUnavailable()
        self.records[request_id] = RequestEvidence(
            request_id,
            principal.tenant_id,
            principal.application_id,
            "completed",
            snapshot.policy_version,
            None,
            {
                **(snapshot.routing_evidence or {}),
                "cache_hit": True,
                "source_request_id": str(source_request_id),
            },
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
