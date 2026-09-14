from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.models import (
    Dispatch,
    ExecutionSnapshot,
    FinishReason,
    JSONSchema,
    Principal,
    RequestEvidence,
    StateUnavailable,
    TokenUsage,
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

    async def authenticate(self, key_hash: str) -> Principal | None:
        if not self.available:
            raise StateUnavailable()
        return self.principal if key_hash == hash_api_key(self.key) else None

    async def ready(self) -> bool:
        return self.available

    async def snapshot(self) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            uuid4(), "mock-v1", uuid4(), uuid4(), "mock-free-v1", Decimal("0"), Decimal("0")
        )

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
        )
        return Dispatch(request_id, uuid4())

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
