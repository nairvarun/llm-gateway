"""Validated, provider-neutral routing snapshots and deterministic selection.

Live profiles deliberately have no token bound until an adapter-specific upper
bound is established. A missing bound is an exclusion, never a cheap estimate.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


def content_hash(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class PricingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    # Must cover all applicable tiers and surcharges; discounts are never used.
    maximum_input_multiplier: Decimal = Field(default=Decimal("1"), ge=1)
    maximum_output_multiplier: Decimal = Field(default=Decimal("1"), ge=1)


class ModelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    pricing_version: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    tasks: frozenset[Literal["generation", "extraction", "classification", "summarization"]]
    structured_output: bool
    context_limit: int = Field(gt=0)
    output_limit: int = Field(gt=0)
    token_bound: Literal["mock_utf8_bytes", "unavailable"] = "unavailable"
    quality_score: Decimal = Field(ge=0, le=1)
    latency_score: Decimal = Field(ge=0, le=1)
    expected_latency_ms: int = Field(default=0, ge=0)
    tenant_allowlist: frozenset[UUID] | None = None

    @model_validator(mode="after")
    def mock_bound_only_for_mock(self) -> "ModelPayload":
        if self.token_bound == "mock_utf8_bytes" and self.provider != "mock":
            raise ValueError("The synthetic mock bound cannot be used for a live provider")
        if not self.tasks:
            raise ValueError("At least one task is required")
        return self


class CandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    order: int = Field(ge=0)


class PolicyWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quality: Decimal = Field(ge=0)
    affordability: Decimal = Field(ge=0)
    latency: Decimal = Field(ge=0)
    health: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def positive_total(self) -> "PolicyWeights":
        if sum((self.quality, self.affordability, self.latency, self.health)) <= 0:
            raise ValueError("At least one weight must be positive")
        return self


class PolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[CandidatePayload, ...] = Field(min_length=1)
    quality_tiers: frozenset[Literal["economy", "standard", "high"]] = frozenset(
        {"economy", "standard", "high"}
    )
    tenant_allowlist: frozenset[UUID] | None = None
    weights: PolicyWeights
    affordability_reference_usd: Decimal = Field(gt=0)
    minimum_deadline_ms: int = Field(ge=0)
    attempt_limit: int = Field(ge=1, le=4)
    fallback_enabled: bool = False

    @model_validator(mode="after")
    def unique_candidates(self) -> "PolicyPayload":
        keys = [(item.name, item.version) for item in self.candidates]
        if len(keys) != len(set(keys)) or len(set(item.order for item in self.candidates)) != len(
            keys
        ):
            raise ValueError("Candidate identities and order values must be unique")
        return self


@dataclass(frozen=True)
class ModelSnapshot:
    id: UUID
    version: str
    payload: ModelPayload
    pricing_id: UUID
    pricing_version: str
    pricing: PricingPayload


@dataclass(frozen=True)
class RegistrySnapshot:
    policy_id: UUID
    policy_name: str
    policy_version: str
    policy: PolicyPayload
    models: tuple[ModelSnapshot, ...]
    disabled_providers: frozenset[str]


@dataclass(frozen=True)
class RoutingInput:
    tenant_id: UUID
    task_type: str
    quality_tier: str
    text: str
    schema: dict[str, object] | None
    max_output_tokens: int
    remaining_deadline_ms: int
    max_cost_usd: Decimal | None
    health: dict[str, Decimal]
    available_adapters: frozenset[tuple[str, str]] | None = None


@dataclass(frozen=True)
class CandidateEvidence:
    provider: str
    model: str
    model_version: str
    pricing_version: str
    eligible: bool
    reasons: tuple[str, ...]
    score: Decimal | None
    estimated_max_cost_usd: Decimal | None


@dataclass(frozen=True)
class RoutingDecision:
    selected: ModelSnapshot | None
    ranked: tuple[CandidateEvidence, ...]


def sanitized_evidence(snapshot: RegistrySnapshot, decision: RoutingDecision) -> dict[str, object]:
    return {
        "policy_name": snapshot.policy_name,
        "policy_version": snapshot.policy_version,
        "policy_id": str(snapshot.policy_id),
        "selected_model_id": str(decision.selected.id) if decision.selected else None,
        "selected_pricing_id": str(decision.selected.pricing_id) if decision.selected else None,
        "candidates": [
            {
                "provider": item.provider,
                "model": item.model,
                "model_version": item.model_version,
                "pricing_version": item.pricing_version,
                "eligible": item.eligible,
                "reasons": list(item.reasons),
                "score": str(item.score) if item.score is not None else None,
                "estimated_max_cost_usd": (
                    str(item.estimated_max_cost_usd)
                    if item.estimated_max_cost_usd is not None
                    else None
                ),
            }
            for item in decision.ranked
        ],
    }


def conservative_input_bound(
    model: ModelPayload, text: str, schema: dict[str, object] | None
) -> int | None:
    if model.token_bound != "mock_utf8_bytes":
        return None
    # Synthetic mock reports <= one token per four code points, and has no
    # schema/system prompt or provider-side hidden wrapper. Bytes dominate it.
    # Reserve an extra local envelope margin for future mock prompt changes.
    schema_bytes = len(json.dumps(schema, sort_keys=True).encode()) if schema else 0
    return len(text.encode()) + schema_bytes + 1024


def maximum_cost(pricing: PricingPayload, input_tokens: int, output_tokens: int) -> Decimal:
    total = (
        Decimal(input_tokens) * pricing.input_per_million * pricing.maximum_input_multiplier
        + Decimal(output_tokens) * pricing.output_per_million * pricing.maximum_output_multiplier
    ) / Decimal(1_000_000)
    if total == 0:
        return Decimal("0")
    return total.quantize(Decimal("0.0000000001"), rounding=ROUND_UP)


def rank_candidates(snapshot: RegistrySnapshot, request: RoutingInput) -> RoutingDecision:
    entries: list[tuple[CandidateEvidence, ModelSnapshot | None, int]] = []
    policy = snapshot.policy
    for candidate in sorted(policy.candidates, key=lambda item: item.order):
        matches = [
            item
            for item in snapshot.models
            if item.payload.model == candidate.name and item.version == candidate.version
        ]
        # Valid registry resolution forbids an ambiguous match; this is a
        # defensive failure if an invalid in-memory snapshot reaches the router.
        if len(matches) != 1:
            raise ValueError("Candidate did not resolve to exactly one model")
        model = matches[0]
        profile = model.payload
        reasons: list[str] = []
        if not profile.enabled or profile.provider in snapshot.disabled_providers:
            reasons.append("disabled")
        if (
            request.available_adapters is not None
            and (profile.provider, profile.model) not in request.available_adapters
        ):
            reasons.append("adapter_unavailable")
        if request.task_type not in profile.tasks:
            reasons.append("task_unsupported")
        if request.schema is not None and not profile.structured_output:
            reasons.append("schema_unsupported")
        if request.quality_tier not in policy.quality_tiers:
            reasons.append("quality_tier")
        if policy.tenant_allowlist is not None and request.tenant_id not in policy.tenant_allowlist:
            reasons.append("tenant_policy")
        if (
            profile.tenant_allowlist is not None
            and request.tenant_id not in profile.tenant_allowlist
        ):
            reasons.append("tenant_model")
        if request.max_output_tokens > profile.output_limit:
            reasons.append("output_limit")
        if request.remaining_deadline_ms < max(
            policy.minimum_deadline_ms, profile.expected_latency_ms
        ):
            reasons.append("deadline")
        health = request.health.get(profile.provider, Decimal("0"))
        if not Decimal("0") <= health <= Decimal("1") or health == 0:
            reasons.append("unhealthy")
        bound = conservative_input_bound(profile, request.text, request.schema)
        estimate: Decimal | None = None
        if bound is None:
            reasons.append("token_bound_unavailable")
        else:
            if bound + request.max_output_tokens > profile.context_limit:
                reasons.append("context_limit")
            estimate = maximum_cost(model.pricing, bound, request.max_output_tokens)
            if request.max_cost_usd is not None and estimate > request.max_cost_usd:
                reasons.append("spend_allowance")
        score: Decimal | None = None
        if not reasons and estimate is not None:
            affordability = max(
                Decimal("0"),
                Decimal("1") - min(Decimal("1"), estimate / policy.affordability_reference_usd),
            )
            weights = policy.weights
            score = (
                weights.quality * profile.quality_score
                + weights.affordability * affordability
                + weights.latency * profile.latency_score
                + weights.health * health
            ) / (weights.quality + weights.affordability + weights.latency + weights.health)
        entries.append(
            (
                CandidateEvidence(
                    profile.provider,
                    profile.model,
                    model.version,
                    model.pricing_version,
                    not reasons,
                    tuple(reasons),
                    score,
                    estimate,
                ),
                model,
                candidate.order,
            )
        )
    # Stable tie break: highest score, then provider/model identity. Policy
    # order is retained in the candidate evidence but not a hidden score.
    entries.sort(
        key=lambda item: (
            item[0].score is None,
            -(item[0].score or Decimal("0")),
            item[0].provider,
            item[0].model,
            item[2],
        )
    )
    selected = next((model for evidence, model, _ in entries if evidence.eligible), None)
    return RoutingDecision(selected, tuple(evidence for evidence, _, _ in entries))
