from dataclasses import replace
from uuid import UUID, uuid4

from app.api.schemas import ExtractRequest, GenerateRequest
from app.cache.identity import eligibility, exact_identity
from app.domain.models import JSONSchema
from app.persistence.bootstrap import DEMO_SCHEMA
from tests.fakes import MemoryStore


def key(request: GenerateRequest, store: MemoryStore, schema: JSONSchema | None = None) -> str:
    return exact_identity(
        b"synthetic-hmac-key",
        store.principal,
        "/v1/extract" if schema is not None else "/v1/generate",
        request,
        schema,
        store.routing,
    )


def test_exact_identity_preserves_whitespace_and_isolates_scopes() -> None:
    first = MemoryStore()
    request = GenerateRequest(input="a b")
    base = key(request, first)
    assert key(request, first) == base
    assert key(request.model_copy(update={"input": "a  b"}), first) != base
    assert key(request.model_copy(update={"temperature": 0.5}), first) != base
    assert key(request.model_copy(update={"max_output_tokens": 123}), first) != base
    assert key(request.model_copy(update={"quality_tier": "high"}), first) != base
    assert key(request.model_copy(update={"metadata": {"variant": "2"}}), first) != base
    assert key(request.model_copy(update={"model_policy": "mock-v1"}), first) != base
    assert key(request, first, DEMO_SCHEMA) != base
    assert key(request, replace_store(first, tenant_id=uuid4())) != base
    assert key(request, replace_store(first, application_id="other")) != base
    assert key(request, replace_store(first, policy_version="v3")) != base
    # Modes and idempotency keys do not change the provider's output.
    assert key(request.model_copy(update={"cache_mode": "read_only"}), first) == base
    assert key(request.model_copy(update={"idempotency_key": "x"}), first) == base


def replace_store(
    source: MemoryStore,
    *,
    tenant_id: UUID | None = None,
    application_id: str | None = None,
    policy_version: str | None = None,
) -> MemoryStore:
    other = MemoryStore()
    other.principal = replace(
        source.principal,
        tenant_id=tenant_id or source.principal.tenant_id,
        application_id=application_id or source.principal.application_id,
    )
    other.routing = replace(
        source.routing, policy_version=policy_version or source.routing.policy_version
    )
    return other


def test_resolved_schema_and_opt_in_eligibility() -> None:
    store = MemoryStore()
    request = ExtractRequest(input='{"count":1}', json_schema=DEMO_SCHEMA)
    changed = {**DEMO_SCHEMA, "description": "another version"}
    assert key(request, store, DEMO_SCHEMA) != key(request, store, changed)
    assert not eligibility(request, True).eligible
    opt_in = request.model_copy(
        update={"cache_mode": "read_write", "cache_classification": "approved_non_sensitive"}
    )
    assert eligibility(opt_in, True).eligible
    assert eligibility(opt_in, False).reason == "application_not_approved"
    assert eligibility(opt_in.model_copy(update={"temperature": 0.4}), True).reason == (
        "nondeterministic_sampling"
    )
    assert eligibility(
        opt_in.model_copy(update={"cache_classification": "sensitive"}), True
    ).reason == ("sensitive_or_unclassified")
