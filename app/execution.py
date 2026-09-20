"""Mock-backed bounded attempt execution; live dispatch remains gated elsewhere."""

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from random import Random
from typing import cast
from uuid import UUID

from app.api.schema_validation import validate_output
from app.api.schemas import GenerateRequest
from app.domain.control import Admission, Control, ControlRejected
from app.domain.deadline import Deadline, DeadlineExpired
from app.domain.errors import GatewayError
from app.domain.models import (
    BudgetExceeded,
    Dispatch,
    ExecutionSnapshot,
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
from app.domain.retry import RETRYABLE, retry_delay
from app.domain.routing import (
    ModelSnapshot,
    RegistrySnapshot,
    RoutingDecision,
    maximum_cost,
)


@dataclass(frozen=True)
class AttemptOutcome:
    output: JSONValue
    provider: str
    model: str
    finish_reason: FinishReason
    usage: TokenUsage
    estimated_cost_usd: Decimal
    attempt_count: int
    fallback_used: bool


def combined_usage(usages: list[TokenUsage]) -> TokenUsage:
    if not usages:
        return TokenUsage(None, None, "unknown")
    inputs = [item.input_tokens for item in usages]
    outputs = [item.output_tokens for item in usages]
    complete = all(value is not None for value in inputs + outputs)
    return TokenUsage(
        sum(cast(int, value) for value in inputs)
        if all(value is not None for value in inputs)
        else None,
        sum(cast(int, value) for value in outputs)
        if all(value is not None for value in outputs)
        else None,
        (
            usages[0].status
            if complete and all(item.status == usages[0].status for item in usages)
            else "observed"
            if complete
            else "partial_unknown"
        ),
    )


class AttemptExecutor:
    def __init__(
        self,
        store: Store,
        providers: dict[tuple[str, str], Provider],
        random: Random | None = None,
        control: Control | None = None,
    ) -> None:
        self.store, self.providers = store, providers
        self.random = random or Random()
        self.control = control

    async def run(
        self,
        principal: Principal,
        request_id: UUID,
        endpoint: str,
        input_hash: str,
        schema_hash: str | None,
        request: GenerateRequest,
        schema: JSONSchema | None,
        registry: RegistrySnapshot,
        decision: RoutingDecision,
        evidence: dict[str, JSONValue],
        deadline: Deadline,
    ) -> AttemptOutcome:
        assert request.max_cost_usd is not None  # Service resolves the server ceiling first.
        eligible = [item for item in decision.ranked if item.eligible]
        if not registry.policy.fallback_enabled:
            eligible = eligible[:1]
        models = {
            (item.payload.provider, item.payload.model, item.version): item
            for item in registry.models
        }
        attempts = 0
        total_cost = Decimal("0")
        usages: list[TokenUsage] = []
        last_error = GatewayError("PROVIDER_UNAVAILABLE", "No provider completed the request.", 503)
        retry_outlives_deadline = False
        terminal_refusal = False
        validation_retry = False
        validation_failed = False
        credential_blocked: set[str] = set()
        for candidate_index, candidate in enumerate(eligible):
            if candidate.provider in credential_blocked:
                continue
            model = models[(candidate.provider, candidate.model, candidate.model_version)]
            minimum_ms = max(registry.policy.minimum_deadline_ms, model.payload.expected_latency_ms)
            candidate_deadline_excluded = False
            adapter = self.providers[(candidate.provider, candidate.model)]
            upper_cost = candidate.estimated_max_cost_usd
            assert upper_cost is not None
            for retry_index in range(3):
                if attempts >= registry.policy.attempt_limit:
                    break
                if deadline.remaining(reserve_recording=True) * 1000 < max(1, minimum_ms):
                    last_error = GatewayError("DEADLINE_EXCEEDED", "Request deadline expired.", 504)
                    candidate_deadline_excluded = True
                    break
                if (
                    request.max_cost_usd is not None
                    and total_cost + upper_cost > request.max_cost_usd
                ):
                    last_error = GatewayError(
                        "BUDGET_EXCEEDED", "Request cost ceiling is exhausted.", 429
                    )
                    break
                snapshot = self._snapshot(registry, model, evidence, upper_cost)
                try:
                    start_attempt = (
                        partial(
                            self.store.begin,
                            principal,
                            request_id,
                            endpoint,
                            input_hash,
                            schema_hash,
                            snapshot,
                            request.max_cost_usd,
                        )
                        if attempts == 0
                        else partial(self.store.add_attempt, request_id, attempts + 1, snapshot)
                    )
                    dispatch = await deadline.run(
                        start_attempt,
                        reserve_recording=True,
                    )
                except DeadlineExpired:
                    last_error = GatewayError("DEADLINE_EXCEEDED", "Request deadline expired.", 504)
                    break
                except BudgetExceeded as error:
                    if attempts == 0:
                        await deadline.run(
                            lambda: self.store.reject_routing(
                                principal,
                                request_id,
                                endpoint,
                                input_hash,
                                schema_hash,
                                registry.policy_id,
                                registry.policy_version,
                                evidence,
                                "BUDGET_EXCEEDED",
                            )
                        )
                    else:
                        await self._complete(deadline, request_id, "failed", "BUDGET_EXCEEDED")
                    raise GatewayError(
                        "BUDGET_EXCEEDED", "Request or tenant allowance is exhausted.", 429
                    ) from error
                attempts += 1
                usage = TokenUsage(None, None, "unknown")
                cost = upper_cost
                finish: FinishReason | None = None
                output: JSONValue = None
                failure: ProviderFailure | None = None
                status = "failed"
                error_class: str | None = None
                invoked = False
                lease: Admission | None = None
                try:
                    if not await deadline.run(
                        partial(self.store.provider_enabled, model.payload.provider),
                        reserve_recording=True,
                    ):
                        cost = Decimal("0")
                        status, error_class = "not_dispatched", "provider_disabled"
                        last_error = GatewayError("NO_ELIGIBLE_MODEL", "Provider is disabled.", 503)
                    else:
                        if self.control is not None:
                            lease = await self.control.acquire(
                                principal.tenant_id, model.payload.provider, deadline
                            )
                        if deadline.remaining(reserve_recording=True) * 1000 < max(1, minimum_ms):
                            candidate_deadline_excluded = True
                            raise DeadlineExpired()
                        invoked = True
                        result = await deadline.run(
                            partial(
                                adapter.invoke,
                                ProviderInput(
                                    request.input,
                                    request.task_type,
                                    schema,
                                    request.temperature,
                                    request.max_output_tokens,
                                    validation_retry,
                                ),
                            ),
                            reserve_recording=True,
                        )
                        usage, finish = result.usage, result.finish_reason
                        cost = self._observed_or_upper(model, usage, upper_cost)
                        output = result.output
                        if schema is not None:
                            if finish in {FinishReason.LENGTH, FinishReason.REFUSAL}:
                                error_class = (
                                    "output_refusal"
                                    if finish is FinishReason.REFUSAL
                                    else "output_truncation"
                                )
                                terminal_refusal = finish is FinishReason.REFUSAL
                                raise GatewayError(
                                    "OUTPUT_VALIDATION_FAILED",
                                    "Extraction was truncated or refused.",
                                    502,
                                )
                            output = validate_output(result.output, schema)
                        status = "completed"
                except ProviderFailure as error:
                    failure = error
                    usage = error.usage
                    cost = self._observed_or_upper(model, usage, upper_cost)
                    error_class = error.kind.value
                    status = (
                        "uncertain"
                        if error.kind in {FailureKind.TIMEOUT, FailureKind.CONNECTION}
                        else "failed"
                    )
                    last_error = (
                        GatewayError(
                            "UPSTREAM_REQUEST_REJECTED", "Provider rejected the request.", 502
                        )
                        if error.kind is FailureKind.INVALID_REQUEST
                        else GatewayError(
                            "PROVIDER_UNAVAILABLE", "Provider could not complete the request.", 503
                        )
                    )
                except DeadlineExpired:
                    error_class, status = (
                        ("deadline", "uncertain")
                        if invoked
                        else ("deadline_before_dispatch", "not_dispatched")
                    )
                    if not invoked:
                        cost = Decimal("0")
                    last_error = GatewayError("DEADLINE_EXCEEDED", "Request deadline expired.", 504)
                except ControlRejected as error:
                    status, error_class, cost = "not_dispatched", error.reason, Decimal("0")
                    last_error = (
                        GatewayError("RATE_LIMITED", "Shared rate limit reached.", 429)
                        if error.reason == "rate_limit"
                        else GatewayError("CIRCUIT_OPEN", "Provider circuit is unavailable.", 503)
                    )
                except StateUnavailable as error:
                    if not invoked:
                        try:
                            await deadline.run(
                                partial(
                                    self.store.settle_attempt,
                                    dispatch,
                                    "not_dispatched",
                                    None,
                                    usage,
                                    Decimal("0"),
                                    "control_unavailable",
                                )
                            )
                            await self._complete(
                                deadline, request_id, "failed", "DEPENDENCY_UNAVAILABLE"
                            )
                        except (StateUnavailable, DeadlineExpired, GatewayError):
                            pass  # Persisted intent remains for recovery.
                    raise GatewayError(
                        "DEPENDENCY_UNAVAILABLE", "Critical control state is unavailable.", 503
                    ) from error
                except GatewayError as error:
                    error_class = error_class or "output_validation"
                    last_error = error
                    validation_retry = not terminal_refusal
                    validation_failed = True
                except asyncio.CancelledError:
                    await asyncio.shield(
                        self._cancel_attempt(
                            lease, dispatch, request_id, finish, usage, cost, invoked
                        )
                    )
                    raise
                if lease is not None and self.control is not None:
                    circuit_result: bool | None = (
                        True if failure is not None and failure.kind in RETRYABLE else None
                    )
                    if status == "completed":
                        circuit_result = False
                    try:
                        await deadline.run(
                            partial(self.control.release, lease, transient=circuit_result)
                        )
                    except (StateUnavailable, DeadlineExpired):
                        last_error = GatewayError(
                            "DEPENDENCY_UNAVAILABLE",
                            "Shared control outcome could not be recorded.",
                            503,
                        )
                        error_class = "control_unavailable_after_dispatch"
                        if invoked:
                            status = "uncertain"
                try:
                    await deadline.run(
                        partial(
                            self.store.settle_attempt,
                            dispatch,
                            status,
                            finish,
                            usage,
                            cost,
                            error_class,
                        )
                    )
                except (StateUnavailable, DeadlineExpired) as error:
                    raise GatewayError(
                        "DEPENDENCY_UNAVAILABLE",
                        "Attempt outcome could not be recorded; execution may have occurred.",
                        503,
                    ) from error
                usages.append(usage)
                total_cost += cost
                if status == "completed":
                    await self._complete(deadline, request_id, "completed", None)
                    assert finish is not None
                    return AttemptOutcome(
                        output,
                        result.provider,
                        result.model,
                        finish,
                        combined_usage(usages),
                        total_cost,
                        attempts,
                        candidate_index > 0,
                    )
                if last_error.code in {
                    "UPSTREAM_REQUEST_REJECTED",
                    "DEADLINE_EXCEEDED",
                    "DEPENDENCY_UNAVAILABLE",
                }:
                    break
                if terminal_refusal:
                    break
                if error_class == "provider_disabled":
                    break
                if failure is not None and failure.kind is FailureKind.CREDENTIAL:
                    credential_blocked.add(candidate.provider)
                    break
                if failure is not None and failure.kind not in RETRYABLE:
                    break
                if retry_index >= 2 or attempts >= registry.policy.attempt_limit:
                    break
                if failure is None and last_error.code != "OUTPUT_VALIDATION_FAILED":
                    break
                delay = (
                    retry_delay(failure, retry_index + 1, self.random)
                    if failure is not None
                    else 0.0
                )
                try:
                    await deadline.sleep(delay)
                except DeadlineExpired:
                    retry_outlives_deadline = True
                    break
            if terminal_refusal or last_error.code in {
                "UPSTREAM_REQUEST_REJECTED",
                "DEPENDENCY_UNAVAILABLE",
            }:
                break
            if last_error.code == "DEADLINE_EXCEEDED" and not candidate_deadline_excluded:
                break
        if attempts:
            if retry_outlives_deadline or last_error.code == "DEADLINE_EXCEEDED":
                last_error = GatewayError("DEADLINE_EXCEEDED", "Request deadline expired.", 504)
            elif validation_failed and last_error.code in {
                "PROVIDER_UNAVAILABLE",
                "NO_ELIGIBLE_MODEL",
                "CIRCUIT_OPEN",
            }:
                last_error = GatewayError(
                    "OUTPUT_VALIDATION_FAILED", "No valid extraction output was returned.", 502
                )
            await self._complete(deadline, request_id, "failed", last_error.code)
        raise last_error

    @staticmethod
    def _snapshot(
        registry: RegistrySnapshot,
        model: ModelSnapshot,
        evidence: dict[str, JSONValue],
        upper_cost: Decimal,
    ) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            registry.policy_id,
            registry.policy_version,
            model.id,
            model.pricing_id,
            model.pricing_version,
            model.pricing.input_per_million,
            model.pricing.output_per_million,
            model.payload.provider,
            model.payload.model,
            evidence,
            upper_cost,
        )

    @staticmethod
    def _observed_or_upper(model: ModelSnapshot, usage: TokenUsage, upper: Decimal) -> Decimal:
        if usage.input_tokens is None or usage.output_tokens is None:
            return upper
        return maximum_cost(model.pricing, usage.input_tokens, usage.output_tokens)

    async def _complete(
        self, deadline: Deadline, request_id: UUID, status: str, error_code: str | None
    ) -> None:
        try:
            await deadline.run(lambda: self.store.complete_request(request_id, status, error_code))
        except (StateUnavailable, DeadlineExpired) as error:
            raise GatewayError(
                "DEPENDENCY_UNAVAILABLE",
                "Request outcome could not be recorded; execution may have occurred.",
                503,
            ) from error

    async def _cancel_attempt(
        self,
        lease: Admission | None,
        dispatch: Dispatch,
        request_id: UUID,
        finish: FinishReason | None,
        usage: TokenUsage,
        cost: Decimal,
        invoked: bool,
    ) -> None:
        if lease is not None and self.control is not None:
            try:
                await self.control.release(lease, transient=True if invoked else None)
            except StateUnavailable:
                pass  # The lease expires; durable attempt is authoritative.
        await self.store.settle_attempt(
            dispatch,
            "uncertain" if invoked else "not_dispatched",
            finish,
            usage,
            cost if invoked else Decimal("0"),
            "cancelled",
        )
        await self.store.complete_request(
            request_id, "uncertain" if invoked else "failed", "EXECUTION_UNCERTAIN"
        )
        if invoked:
            await self.store.mark_idempotency_uncertain(request_id)
