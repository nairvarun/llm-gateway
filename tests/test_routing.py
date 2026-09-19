from dataclasses import replace
from decimal import Decimal
from random import Random
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.routing import (
    CandidatePayload,
    ModelPayload,
    ModelSnapshot,
    PolicyPayload,
    PolicyWeights,
    PricingPayload,
    RegistrySnapshot,
    RoutingInput,
    maximum_cost,
    rank_candidates,
)


def profile(provider: str, model: str, *, quality: str = "0.5") -> ModelSnapshot:
    return ModelSnapshot(
        uuid4(),
        "v1",
        ModelPayload(
            provider=provider,
            model=model,
            pricing_version="v1",
            tasks=frozenset({"generation", "extraction"}),
            structured_output=True,
            context_limit=10_000,
            output_limit=1024,
            token_bound="mock_utf8_bytes" if provider == "mock" else "unavailable",
            quality_score=Decimal(quality),
            latency_score=Decimal("0.5"),
        ),
        uuid4(),
        "v1",
        PricingPayload(input_per_million=Decimal("1"), output_per_million=Decimal("2")),
    )


def registry(*models: ModelSnapshot) -> RegistrySnapshot:
    return RegistrySnapshot(
        uuid4(),
        "test-policy",
        "v1",
        PolicyPayload(
            candidates=tuple(
                CandidatePayload(name=model.payload.model, version=model.version, order=index)
                for index, model in enumerate(models)
            ),
            weights=PolicyWeights(quality=1, affordability=1, latency=1, health=1),
            affordability_reference_usd=Decimal("1"),
            minimum_deadline_ms=100,
            attempt_limit=1,
        ),
        models,
        frozenset(),
    )


def request(**changes: object) -> RoutingInput:
    values: dict[str, object] = dict(
        tenant_id=uuid4(),
        task_type="generation",
        quality_tier="standard",
        text="synthetic",
        schema=None,
        max_output_tokens=512,
        remaining_deadline_ms=1000,
        max_cost_usd=Decimal("1"),
        health={"mock": Decimal("1"), "openai": Decimal("1")},
    )
    values.update(changes)
    return RoutingInput(**values)  # type: ignore[arg-type]


def test_decimal_upper_price_and_rounding() -> None:
    price = PricingPayload(
        input_per_million=Decimal("0.2"),
        output_per_million=Decimal("1.2"),
        maximum_input_multiplier=Decimal("2"),
        maximum_output_multiplier=Decimal("1.5"),
    )
    assert maximum_cost(price, 1_000_000, 500_000) == Decimal("1.3000000000")
    assert maximum_cost(price, 1, 0) == Decimal("0.0000004000")


def test_payload_validation_rejects_unsafe_bound_and_bad_weights() -> None:
    with pytest.raises(ValidationError):
        ModelPayload(
            provider="openai",
            model="live",
            pricing_version="v1",
            tasks={"generation"},
            structured_output=True,
            context_limit=100,
            output_limit=10,
            token_bound="mock_utf8_bytes",
            quality_score=0,
            latency_score=0,
        )
    with pytest.raises(ValidationError):
        PolicyWeights(quality=0, affordability=0, latency=0, health=0)


def test_unavailable_bound_rejected_before_spend_dispatch() -> None:
    live = profile("openai", "live")
    decision = rank_candidates(registry(live), request())
    assert decision.selected is None
    assert decision.ranked[0].reasons == ("token_bound_unavailable",)
    assert decision.ranked[0].estimated_max_cost_usd is None


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"task_type": "classification"}, "task_unsupported"),
        ({"max_output_tokens": 1025}, "output_limit"),
        ({"remaining_deadline_ms": 99}, "deadline"),
        ({"health": {"mock": Decimal("0")}}, "unhealthy"),
        ({"max_cost_usd": Decimal("0.000001")}, "spend_allowance"),
        ({"text": "x" * 10_000}, "context_limit"),
        ({"quality_tier": "not-allowed"}, "quality_tier"),
    ],
)
def test_constraint_exclusions(change: dict[str, object], reason: str) -> None:
    decision = rank_candidates(registry(profile("mock", "mock-a")), request(**change))
    assert decision.selected is None
    assert reason in decision.ranked[0].reasons


def test_tenant_schema_and_disablement_exclusions() -> None:
    model = profile("mock", "mock-a")
    tenant = uuid4()
    model = replace(
        model,
        payload=model.payload.model_copy(
            update={"tenant_allowlist": frozenset({tenant}), "structured_output": False}
        ),
    )
    base = registry(model)
    decision = rank_candidates(
        replace(base, disabled_providers=frozenset({"mock"})), request(schema={"type": "string"})
    )
    assert set(decision.ranked[0].reasons) == {"disabled", "schema_unsupported", "tenant_model"}


def test_policy_tenant_enabled_and_expected_latency_exclusions() -> None:
    model = profile("mock", "mock-a")
    model = replace(
        model,
        payload=model.payload.model_copy(update={"enabled": False, "expected_latency_ms": 900}),
    )
    base = registry(model)
    policy = base.policy.model_copy(update={"tenant_allowlist": frozenset({uuid4()})})
    decision = rank_candidates(replace(base, policy=policy), request(remaining_deadline_ms=500))
    assert set(decision.ranked[0].reasons) == {"disabled", "deadline", "tenant_policy"}


def test_identical_snapshot_order_and_stable_tie_break() -> None:
    first = profile("mock", "mock-z")
    second = profile("mock", "mock-a")
    snapshot = registry(first, second)
    incoming = request()
    results = [rank_candidates(snapshot, incoming) for _ in range(100)]
    assert all(result == results[0] for result in results)
    assert [item.model for item in results[0].ranked] == ["mock-a", "mock-z"]
    assert results[0].selected == second


def test_higher_weighted_score_outranks_policy_order() -> None:
    weak = profile("mock", "mock-weak", quality="0.1")
    strong = profile("mock", "mock-strong", quality="0.9")
    decision = rank_candidates(registry(weak, strong), request())
    assert decision.selected == strong
    assert [item.model for item in decision.ranked] == ["mock-strong", "mock-weak"]


def test_equal_score_order_is_independent_of_candidate_order_property() -> None:
    models = [profile("mock", f"mock-{index:02d}") for index in range(20)]
    incoming = request()
    random = Random(42)
    for _ in range(100):
        random.shuffle(models)
        decision = rank_candidates(registry(*models), incoming)
        assert [item.model for item in decision.ranked] == sorted(
            model.payload.model for model in models
        )
