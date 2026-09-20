from dataclasses import replace
from decimal import Decimal
from random import Random
from uuid import UUID, uuid4

import pytest

from app.api.schemas import ExtractRequest, ExtractResponse, GenerateRequest, GenerateResponse
from app.config import Settings
from app.domain.errors import GatewayError
from app.domain.models import ProviderInput, ProviderResult
from app.domain.routing import CandidatePayload, ModelSnapshot, PricingPayload
from app.persistence.bootstrap import DEMO_SCHEMA
from app.providers.mock import MockProvider, MockStep
from app.service import GatewayService
from tests.fakes import MemoryStore
from tests.test_deadline import FakeClock


def configured_store(*, fallback: bool = False, priced: bool = False) -> MemoryStore:
    store = MemoryStore()
    model = store.routing.models[0]
    if priced:
        model = replace(
            model,
            pricing=PricingPayload(input_per_million=Decimal("1"), output_per_million=0),
        )
    models: tuple[ModelSnapshot, ...] = (model,)
    candidates = store.routing.policy.candidates
    if fallback:
        second = replace(
            model,
            id=uuid4(),
            payload=model.payload.model_copy(update={"model": "mock-text-v2"}),
        )
        models = (model, second)
        candidates += (CandidatePayload(name="mock-text-v2", version="v2", order=1),)
    policy = store.routing.policy.model_copy(
        update={"attempt_limit": 4, "fallback_enabled": fallback, "candidates": candidates}
    )
    store.routing = replace(store.routing, models=models, policy=policy)
    return store


def service(
    store: MemoryStore, provider: MockProvider, *, second: MockProvider | None = None
) -> GatewayService:
    return GatewayService(
        Settings(),
        store,
        provider,
        FakeClock(),
        {("mock", "mock-text-v2"): second} if second is not None else None,
        Random(0),
    )


async def execute(
    gateway: GatewayService, store: MemoryStore, request: GenerateRequest
) -> tuple[UUID, GenerateResponse | ExtractResponse]:
    request_id = uuid4()
    response = await gateway.execute(request, store.principal, request_id, started=0)
    return request_id, response


async def test_transient_retries_are_bounded_and_accounted_separately() -> None:
    store = configured_store()
    provider = MockProvider([MockStep("rate_limit"), MockStep("server"), MockStep()])
    request_id, response = await execute(
        service(store, provider), store, GenerateRequest(input="hello")
    )
    assert provider.invocations == 3
    assert response.usage.attempt_count == 3
    assert response.usage.status == "partial_unknown"
    assert store.records[request_id].status == "completed"
    assert [store.attempt_outcomes[a.attempt_id][0] for a in store.attempts[request_id]] == [
        "failed",
        "failed",
        "completed",
    ]
    assert len(store.usages) == 3


async def test_fallback_after_transient_exhaustion_uses_the_next_profile() -> None:
    store = configured_store(fallback=True)
    first = MockProvider([MockStep("server")])
    second = MockProvider(model="mock-text-v2")
    request_id, response = await execute(
        service(store, first, second=second), store, GenerateRequest(input="x")
    )
    assert (first.invocations, second.invocations) == (3, 1)
    assert response.model == "mock-text-v2"
    assert response.fallback_used is True
    assert response.usage.attempt_count == 4
    assert len(store.attempts[request_id]) == 4


async def test_long_retry_after_uses_fallback_without_waiting() -> None:
    store = configured_store(fallback=True)
    first = MockProvider([MockStep("rate_limit", retry_after_seconds=10)])
    second = MockProvider(model="mock-text-v2")
    gateway = service(store, first, second=second)
    _, response = await execute(gateway, store, GenerateRequest(input="x", latency_budget_ms=500))
    assert (first.invocations, second.invocations) == (1, 1)
    assert response.fallback_used is True
    assert gateway.clock.sleeps == []  # type: ignore[attr-defined]


async def test_no_fallback_for_permanent_invalid_upstream_request() -> None:
    store = configured_store(fallback=True)
    first = MockProvider([MockStep("invalid_request")])
    second = MockProvider(model="mock-text-v2")
    request_id = uuid4()
    with pytest.raises(GatewayError, match="Provider rejected") as caught:
        await service(store, first, second=second).execute(
            GenerateRequest(input="x"), store.principal, request_id, started=0
        )
    assert caught.value.code == "UPSTREAM_REQUEST_REJECTED"
    assert (first.invocations, second.invocations) == (1, 0)
    assert store.records[request_id].error_code == "UPSTREAM_REQUEST_REJECTED"


async def test_credential_failure_does_not_try_another_model_in_same_provider() -> None:
    store = configured_store(fallback=True)
    first = MockProvider([MockStep("credential")])
    second = MockProvider(model="mock-text-v2")
    with pytest.raises(GatewayError) as caught:
        await execute(service(store, first, second=second), store, GenerateRequest(input="x"))
    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert (first.invocations, second.invocations) == (1, 0)


async def test_nontransient_server_fault_has_no_same_candidate_retry() -> None:
    store = configured_store()
    provider = MockProvider([MockStep("server_permanent"), MockStep()])
    with pytest.raises(GatewayError):
        await execute(service(store, provider), store, GenerateRequest(input="x"))
    assert provider.invocations == 1


async def test_extraction_validation_can_recover_but_refusal_never_succeeds() -> None:
    store = configured_store()
    provider = MockProvider([MockStep("malformed"), MockStep()])
    _, response = await execute(
        service(store, provider),
        store,
        ExtractRequest(input='{"count":2}', json_schema=DEMO_SCHEMA),
    )
    assert response.output == {"count": 2}
    assert response.usage.attempt_count == 2
    assert provider.invocations == 2

    refusing = MockProvider([MockStep("refusal")])
    store = configured_store(fallback=True)
    alternate = MockProvider(model="mock-text-v2")
    request_id = uuid4()
    with pytest.raises(GatewayError) as caught:
        await service(store, refusing, second=alternate).execute(
            ExtractRequest(input='{"count":2}', json_schema=DEMO_SCHEMA),
            store.principal,
            request_id,
            started=0,
        )
    assert caught.value.code == "OUTPUT_VALIDATION_FAILED"
    assert refusing.invocations == 1
    assert alternate.invocations == 0
    assert store.records[request_id].status == "failed"


async def test_request_ceiling_bounds_total_attempt_liability() -> None:
    store = configured_store(fallback=True, priced=True)
    first = MockProvider([MockStep("server")])
    second = MockProvider(model="mock-text-v2")
    maximum = "0.001538"  # One conservative admission, not two.
    request_id = uuid4()
    with pytest.raises(GatewayError) as caught:
        await service(store, first, second=second).execute(
            GenerateRequest(input="x", max_cost_usd=maximum),
            store.principal,
            request_id,
            started=0,
        )
    assert caught.value.code == "BUDGET_EXCEEDED"
    assert (first.invocations, second.invocations) == (1, 0)
    assert store.records[request_id].status == "failed"


async def test_server_ceiling_applies_when_caller_omits_or_raises_its_limit() -> None:
    for caller_limit in (None, "1"):
        store = configured_store(priced=True)
        provider = MockProvider([MockStep("server"), MockStep()])
        gateway = GatewayService(
            Settings(default_request_cost_usd=Decimal("0.001538")),
            store,
            provider,
            FakeClock(),
            random=Random(0),
        )
        request_id = uuid4()
        with pytest.raises(GatewayError) as caught:
            await gateway.execute(
                GenerateRequest(input="x", max_cost_usd=caller_limit),
                store.principal,
                request_id,
                started=0,
            )
        assert caught.value.code == "BUDGET_EXCEEDED"
        assert provider.invocations == 1


async def test_cheaper_fallback_can_fit_remaining_original_ceiling() -> None:
    store = configured_store(fallback=True, priced=True)
    first_model, second_model = store.routing.models
    store.routing = replace(
        store.routing,
        models=(
            replace(
                first_model,
                payload=first_model.payload.model_copy(update={"quality_score": Decimal("1")}),
            ),
            replace(
                second_model,
                payload=second_model.payload.model_copy(update={"quality_score": Decimal("0")}),
                pricing=PricingPayload(input_per_million=Decimal("0.5"), output_per_million=0),
            ),
        ),
    )
    first = MockProvider([MockStep("server")])
    second = MockProvider(model="mock-text-v2")
    _, response = await execute(
        service(store, first, second=second),
        store,
        GenerateRequest(input="x", max_cost_usd="0.0016"),
    )
    assert (first.invocations, second.invocations) == (1, 1)
    assert response.fallback_used is True
    assert response.estimated_cost_usd <= Decimal("0.0016")


async def test_no_fallback_when_retry_cannot_fit_deadline() -> None:
    store = configured_store()
    provider = MockProvider([MockStep("rate_limit", retry_after_seconds=5)])
    request_id = uuid4()
    with pytest.raises(GatewayError) as caught:
        await service(store, provider).execute(
            GenerateRequest(input="x", latency_budget_ms=500),
            store.principal,
            request_id,
            started=0,
        )
    assert caught.value.code == "DEADLINE_EXCEEDED"
    assert provider.invocations == 1
    assert store.records[request_id].error_code == "DEADLINE_EXCEEDED"


async def test_invalid_output_exhaustion_and_spend_precedence() -> None:
    request = ExtractRequest(input='{"count":2}', json_schema=DEMO_SCHEMA)
    store = configured_store()
    malformed = MockProvider([MockStep("malformed")])
    request_id = uuid4()
    with pytest.raises(GatewayError) as exhausted:
        await service(store, malformed).execute(request, store.principal, request_id, 0)
    assert exhausted.value.code == "OUTPUT_VALIDATION_FAILED"
    assert malformed.invocations == 3

    store = configured_store(priced=True)
    malformed = MockProvider([MockStep("malformed"), MockStep()])
    request_id = uuid4()
    with pytest.raises(GatewayError) as blocked:
        await service(store, malformed).execute(
            request.model_copy(update={"max_cost_usd": Decimal("0.002")}),
            store.principal,
            request_id,
            0,
        )
    assert blocked.value.code == "BUDGET_EXCEEDED"
    assert malformed.invocations == 1


async def test_invalid_output_deadline_precedes_validation_exhaustion() -> None:
    class AdvancingMock(MockProvider):
        async def invoke(self, request: ProviderInput) -> ProviderResult:
            result = await super().invoke(request)
            clock.time += 0.35
            return result

    clock = FakeClock()
    store = configured_store()
    model = store.routing.models[0]
    store.routing = replace(
        store.routing,
        models=(
            replace(model, payload=model.payload.model_copy(update={"expected_latency_ms": 100})),
        ),
    )
    provider = AdvancingMock([MockStep("malformed"), MockStep()])
    gateway = GatewayService(Settings(), store, provider, clock)
    request_id = uuid4()
    with pytest.raises(GatewayError) as caught:
        await gateway.execute(
            ExtractRequest(input='{"count":2}', json_schema=DEMO_SCHEMA, latency_budget_ms=500),
            store.principal,
            request_id,
            started=0,
        )
    assert caught.value.code == "DEADLINE_EXCEEDED"
    assert provider.invocations == 1
    assert store.records[request_id].error_code == "DEADLINE_EXCEEDED"
