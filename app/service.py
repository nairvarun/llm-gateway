import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from random import Random
from typing import cast
from uuid import UUID

from pydantic import ValidationError

from app.api.schema_validation import validate_output, validate_schema
from app.api.schemas import (
    ExtractRequest,
    ExtractResponse,
    GenerateRequest,
    GenerateResponse,
    UsageResponse,
)
from app.cache.entry import open_entry, seal_entry
from app.cache.identity import eligibility, exact_identity
from app.cache.redis_cache import Cache, CacheUnavailable
from app.config import Settings
from app.domain.control import Control
from app.domain.deadline import Clock, Deadline, SystemClock
from app.domain.errors import GatewayError
from app.domain.models import ExecutionSnapshot, JSONSchema, JSONValue, Principal, Provider, Store
from app.domain.routing import (
    RegistrySnapshot,
    RoutingDecision,
    RoutingInput,
    rank_candidates,
    sanitized_evidence,
)
from app.execution import AttemptExecutor
from app.security.replay import ReplayCipher


class GatewayService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        provider: Provider,
        clock: Clock | None = None,
        providers: Mapping[tuple[str, str], Provider] | None = None,
        random: Random | None = None,
        control: Control | None = None,
        cache: Cache | None = None,
    ) -> None:
        self.settings, self.store, self.provider = settings, store, provider
        self.clock = clock or SystemClock()
        self.providers = {
            (provider.capabilities.provider, provider.capabilities.model): provider,
            **(providers or {}),
        }
        self.executor = AttemptExecutor(store, self.providers, random, control)
        self.cache = cache
        self.replay_cipher = (
            ReplayCipher(settings.replay_encryption_key.get_secret_value())
            if settings.replay_encryption_key is not None
            else None
        )
        self.cache_cipher = (
            ReplayCipher(settings.cache_encryption_key.get_secret_value())
            if settings.cache_encryption_key is not None
            else None
        )

    async def execute(
        self,
        request: GenerateRequest,
        principal: Principal,
        request_id: UUID,
        started: float,
    ) -> GenerateResponse | ExtractResponse:
        deadline = Deadline(self.clock, started, request.latency_budget_ms)
        if len(request.input) > self.settings.input_limit_chars:
            raise GatewayError("INVALID_REQUEST", "Input exceeds the configured limit.", 422)
        if request.model_policy not in {"mock", "mock-v1", "mock@v1"}:
            raise GatewayError(
                "NO_ELIGIBLE_MODEL", "Only the offline mock policy is available.", 503
            )
        schema: JSONSchema | None = None
        schema_hash: str | None = None
        if isinstance(request, ExtractRequest):
            named = request.schema_name is not None or request.schema_version is not None
            if (request.json_schema is not None) == named:
                raise GatewayError(
                    "INVALID_SCHEMA", "Provide exactly one inline or named schema.", 422
                )
            if named:
                if request.schema_name is None or request.schema_version is None:
                    raise GatewayError(
                        "INVALID_SCHEMA", "A schema name requires an explicit version.", 422
                    )
                schema = await deadline.run(
                    lambda: self.store.schema(
                        principal, cast(str, request.schema_name), cast(str, request.schema_version)
                    ),
                    reserve_recording=True,
                )
                if schema is None:
                    raise GatewayError(
                        "INVALID_SCHEMA", "Schema is unavailable in this tenant.", 422
                    )
            else:
                schema = request.json_schema
            assert schema is not None
            schema_hash = validate_schema(
                schema,
                max_bytes=self.settings.schema_limit_bytes,
                max_depth=self.settings.schema_max_depth,
            )
        elif request.task_type == "extraction":
            raise GatewayError("INVALID_REQUEST", "Use /v1/extract for structured extraction.", 422)
        input_hash = hmac.new(
            self.settings.input_hash_key.get_secret_value().encode(),
            request.input.encode(),
            hashlib.sha256,
        ).hexdigest()
        endpoint = "/v1/extract" if schema is not None else "/v1/generate"
        if request.idempotency_key is not None:
            return await self._keyed(
                request, principal, request_id, started, deadline, endpoint, schema
            )
        effective_ceiling = min(
            self.settings.default_request_cost_usd,
            request.max_cost_usd or self.settings.default_request_cost_usd,
        )
        registry = await deadline.run(self.store.registry, reserve_recording=True)
        control = self.executor.control
        health = (
            await deadline.run(lambda: control.health("mock"), reserve_recording=True)
            if control is not None
            else Decimal("1")
        )
        decision = rank_candidates(
            registry,
            RoutingInput(
                tenant_id=principal.tenant_id,
                task_type=request.task_type,
                quality_tier=request.quality_tier,
                text=request.input,
                schema=cast(dict[str, object] | None, schema),
                max_output_tokens=request.max_output_tokens,
                remaining_deadline_ms=max(
                    0, int(deadline.remaining(reserve_recording=True) * 1000)
                ),
                max_cost_usd=effective_ceiling,
                # Only the offline mock can be dispatched by this service.
                health={"mock": health},
                available_adapters=frozenset(key for key in self.providers if key[0] == "mock"),
            ),
        )
        evidence = cast(dict[str, JSONValue], sanitized_evidence(registry, decision))
        evidence["health_snapshot"] = {"mock": str(health)}
        if decision.selected is None or decision.selected.payload.provider != "mock":
            reasons = sorted({reason for item in decision.ranked for reason in item.reasons})
            if decision.selected is not None:
                evidence["dispatch_exclusion"] = "live_dispatch_gated"
                reasons.append("live_dispatch_gated")
            circuit_open = set(reasons) == {"unhealthy"} and health == 0
            error_code = "CIRCUIT_OPEN" if circuit_open else "NO_ELIGIBLE_MODEL"
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
                    error_code,
                ),
            )
            raise GatewayError(
                error_code,
                "No eligible offline model: " + ", ".join(reasons or ["unavailable"]),
                503,
            )
        cache_status = "bypass"
        cache_key: str | None = None
        cache_token: str | None = None
        if request.cache_mode != "bypass":
            approved = await deadline.run(
                lambda: self.store.cache_approved(principal), reserve_recording=True
            )
            cache_eligibility = eligibility(request, approved)
            cache_status = "miss" if cache_eligibility.eligible else "ineligible"
            if cache_eligibility.eligible:
                if self.cache is None or self.cache_cipher is None:
                    cache_status = "degraded"
                else:
                    cache = self.cache
                    identity = exact_identity(
                        self.cache_cipher._key, principal, endpoint, request, schema, registry
                    )
                    namespace_generation, exact_generation = await deadline.run(
                        lambda: self.store.cache_generations(principal, identity),
                        reserve_recording=True,
                    )
                    active_cache_key = f"{namespace_generation}:{exact_generation}:{identity}"
                    cache_key = active_cache_key
                    evidence["cache_key_hash"] = identity
                    try:
                        cached = await self._cached_response(
                            principal,
                            request_id,
                            started,
                            request,
                            endpoint,
                            schema,
                            input_hash,
                            schema_hash,
                            effective_ceiling,
                            registry,
                            decision,
                            evidence,
                            active_cache_key,
                            identity,
                            deadline,
                        )
                        if cached is not None:
                            return cached
                        if request.cache_mode == "read_write":
                            while cache_token is None:
                                cache_token = await deadline.run(
                                    lambda: cache.claim(
                                        active_cache_key,
                                        max(
                                            1,
                                            int(deadline.remaining(reserve_recording=True) * 1000),
                                        ),
                                    ),
                                    reserve_recording=True,
                                )
                                if cache_token is not None:
                                    # The prior owner may have published between our
                                    # last read and this newly acquired lease.
                                    cached = await self._cached_response(
                                        principal,
                                        request_id,
                                        started,
                                        request,
                                        endpoint,
                                        schema,
                                        input_hash,
                                        schema_hash,
                                        effective_ceiling,
                                        registry,
                                        decision,
                                        evidence,
                                        active_cache_key,
                                        identity,
                                        deadline,
                                    )
                                    if cached is not None:
                                        try:
                                            await cache.release(active_cache_key, cache_token)
                                        except CacheUnavailable:
                                            pass  # The lease expires; no provider work follows.
                                        return cached
                                    break
                                await deadline.sleep(0.02)
                                cached = await self._cached_response(
                                    principal,
                                    request_id,
                                    started,
                                    request,
                                    endpoint,
                                    schema,
                                    input_hash,
                                    schema_hash,
                                    effective_ceiling,
                                    registry,
                                    decision,
                                    evidence,
                                    active_cache_key,
                                    identity,
                                    deadline,
                                )
                                if cached is not None:
                                    return cached
                    except CacheUnavailable:
                        # A cache-only fault may degrade, but controls must still work.
                        if control is not None:
                            await deadline.run(
                                lambda: control.health("mock"), reserve_recording=True
                            )
                        cache_status = "degraded"
                        cache_key = None
                        cache_token = None
        try:
            outcome = await self.executor.run(
                principal,
                request_id,
                endpoint,
                input_hash,
                schema_hash,
                request.model_copy(update={"max_cost_usd": effective_ceiling}),
                schema,
                registry,
                decision,
                evidence,
                deadline,
            )
        except BaseException:
            if cache_token is not None and self.cache is not None and cache_key is not None:
                try:
                    await self.cache.release(cache_key, cache_token)
                except CacheUnavailable:
                    pass
            raise
        common = {
            "request_id": request_id,
            "provider": outcome.provider,
            "model": outcome.model,
            "finish_reason": outcome.finish_reason,
            "usage": UsageResponse(
                input_tokens=outcome.usage.input_tokens,
                output_tokens=outcome.usage.output_tokens,
                status=outcome.usage.status,
                complete=outcome.usage.input_tokens is not None
                and outcome.usage.output_tokens is not None,
                attempt_count=outcome.attempt_count,
            ),
            "estimated_cost_usd": outcome.estimated_cost_usd,
            "latency_ms": (self.clock.now() - started) * 1000,
            "policy_version": registry.policy_version,
            "routing": evidence,
            "fallback_used": outcome.fallback_used,
            "cache_status": cache_status,
        }
        if schema is not None:
            response: GenerateResponse | ExtractResponse = ExtractResponse.model_validate(
                {**common, "output": outcome.output}
            )
        else:
            response = GenerateResponse.model_validate({**common, "output": outcome.output})
        if cache_token is not None and self.cache is not None and cache_key is not None:
            assert self.cache_cipher is not None
            try:
                content = seal_entry(
                    self.cache_cipher,
                    principal.tenant_id,
                    endpoint,
                    cache_key,
                    request_id,
                    response.model_dump(mode="json"),
                    self.settings.cache_ttl_seconds,
                )
                published = await self.cache.publish(
                    cache_key, cache_token, content, self.settings.cache_ttl_seconds
                )
                if not published:
                    response.cache_status = "degraded"
            except CacheUnavailable:
                response.cache_status = "degraded"
        return response

    async def _cached_response(
        self,
        principal: Principal,
        request_id: UUID,
        started: float,
        request: GenerateRequest,
        endpoint: str,
        schema: JSONSchema | None,
        input_hash: str,
        schema_hash: str | None,
        effective_ceiling: Decimal,
        registry: RegistrySnapshot,
        decision: RoutingDecision,
        evidence: dict[str, JSONValue],
        cache_key: str,
        identity: str,
        deadline: Deadline,
    ) -> GenerateResponse | ExtractResponse | None:
        cache = self.cache
        assert cache is not None and self.cache_cipher is not None
        raw = await deadline.run(lambda: cache.read(cache_key), reserve_recording=True)
        if raw is None:
            return None
        entry = open_entry(self.cache_cipher, principal.tenant_id, endpoint, cache_key, raw)
        if entry is None:
            return None
        current_generations = await deadline.run(
            lambda: self.store.cache_generations(principal, identity),
            reserve_recording=True,
        )
        if cache_key != f"{current_generations[0]}:{current_generations[1]}:{identity}":
            return None
        try:
            source: GenerateResponse | ExtractResponse
            if isinstance(request, ExtractRequest):
                source = ExtractResponse.model_validate(entry.response)
            else:
                source = GenerateResponse.model_validate(entry.response)
        except ValidationError:
            return None
        if (
            source.request_id != entry.source_request_id
            or source.policy_version != registry.policy_version
        ):
            return None
        candidate = next(
            (
                model
                for model in registry.models
                if model.payload.provider == source.provider
                and model.payload.model == source.model
                and any(
                    item.eligible
                    and item.provider == source.provider
                    and item.model == source.model
                    for item in decision.ranked
                )
            ),
            None,
        )
        if candidate is None:
            return None
        if not await deadline.run(
            lambda: self.store.provider_enabled(source.provider), reserve_recording=True
        ):
            return None
        if schema is not None:
            try:
                validate_output(json.dumps(source.output), schema)
            except GatewayError:
                return None
        snapshot = ExecutionSnapshot(
            registry.policy_id,
            registry.policy_version,
            candidate.id,
            candidate.pricing_id,
            candidate.pricing_version,
            candidate.pricing.input_per_million,
            candidate.pricing.output_per_million,
            candidate.payload.provider,
            candidate.payload.model,
            evidence,
            Decimal("0"),
        )
        await deadline.run(
            lambda: self.store.record_cache_hit(
                principal,
                request_id,
                endpoint,
                input_hash,
                schema_hash,
                snapshot,
                entry.source_request_id,
                effective_ceiling,
            ),
            reserve_recording=True,
        )
        revised = source.model_dump(mode="json")
        revised.update(
            request_id=request_id,
            original_request_id=entry.source_request_id,
            cache_hit=True,
            cache_status="hit",
            estimated_cost_usd="0",
            latency_ms=(self.clock.now() - started) * 1000,
            usage={
                "input_tokens": 0,
                "output_tokens": 0,
                "status": "cache",
                "complete": True,
                "attempt_count": 0,
                "source_request_id": entry.source_request_id,
            },
        )
        if isinstance(request, ExtractRequest):
            return ExtractResponse.model_validate(revised)
        return GenerateResponse.model_validate(revised)

    async def _keyed(
        self,
        request: GenerateRequest,
        principal: Principal,
        request_id: UUID,
        started: float,
        deadline: Deadline,
        endpoint: str,
        schema: JSONSchema | None,
    ) -> GenerateResponse | ExtractResponse:
        cipher = self.replay_cipher
        if cipher is None:
            raise GatewayError(
                "DEPENDENCY_UNAVAILABLE", "Keyed execution requires a configured replay key.", 503
            )
        assert request.idempotency_key is not None
        key_hash = cipher.key_hash(principal.tenant_id, endpoint, request.idempotency_key)
        fingerprint = cipher.fingerprint(principal, endpoint, request, schema)
        now = datetime.now(UTC)
        claim = await deadline.run(
            lambda: self.store.claim_idempotency(
                principal,
                endpoint,
                key_hash,
                fingerprint,
                request_id,
                now + timedelta(hours=self.settings.replay_retention_hours),
                now + timedelta(milliseconds=request.latency_budget_ms + 30_000),
            ),
            reserve_recording=True,
        )
        if claim.status == "conflict":
            raise GatewayError("IDEMPOTENCY_CONFLICT", "Key was used with different inputs.", 409)
        if claim.status == "in_progress":
            raise GatewayError(
                "REQUEST_IN_PROGRESS",
                "Original execution is still in progress.",
                409,
                retryable=True,
                original_request_id=claim.original_request_id,
                retry_after_seconds=1,
            )
        if claim.status == "uncertain":
            raise GatewayError(
                "EXECUTION_UNCERTAIN",
                "Original execution requires reconciliation.",
                409,
                original_request_id=claim.original_request_id,
            )
        if claim.status == "replay":
            if claim.encrypted_result is None:
                raise GatewayError("EXECUTION_UNCERTAIN", "Replay content is unavailable.", 409)
            content = cipher.open(
                principal.tenant_id,
                endpoint,
                key_hash,
                claim.original_request_id,
                claim.encrypted_result,
            )
            if content.get("kind") == "error":
                raise GatewayError(
                    str(content["code"]),
                    str(content["message"]),
                    int(content["status"]),
                    original_request_id=claim.original_request_id,
                )
            if content.get("kind") != "success" or not isinstance(content.get("response"), dict):
                raise GatewayError("EXECUTION_UNCERTAIN", "Replay content is unavailable.", 409)
            source = dict(content["response"])
            source["request_id"] = request_id
            source["original_request_id"] = claim.original_request_id
            source["idempotency_replayed"] = True
            source["estimated_cost_usd"] = "0"
            source["latency_ms"] = (self.clock.now() - started) * 1000
            source["usage"] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "status": "replay",
                "complete": True,
                "attempt_count": 0,
                "source_request_id": claim.original_request_id,
            }
            if isinstance(request, ExtractRequest):
                return ExtractResponse.model_validate(source)
            return GenerateResponse.model_validate(source)
        if claim.status != "owned":
            raise GatewayError("EXECUTION_UNCERTAIN", "Keyed execution state is invalid.", 409)
        try:
            response = await self.execute(
                request.model_copy(update={"idempotency_key": None}),
                principal,
                request_id,
                started,
            )
        except GatewayError as error:
            evidence = await deadline.run(lambda: self.store.evidence(principal, request_id))
            if evidence is not None and evidence.status == "failed":
                encrypted = cipher.seal(
                    principal.tenant_id,
                    endpoint,
                    key_hash,
                    request_id,
                    {
                        "kind": "error",
                        "code": error.code,
                        "message": error.message,
                        "status": error.status,
                    },
                )
                await deadline.run(
                    lambda: self.store.seal_idempotency(
                        principal.tenant_id, endpoint, key_hash, request_id, "failed", encrypted
                    )
                )
            raise
        encrypted = cipher.seal(
            principal.tenant_id,
            endpoint,
            key_hash,
            request_id,
            {"kind": "success", "response": response.model_dump(mode="json")},
        )
        await deadline.run(
            lambda: self.store.seal_idempotency(
                principal.tenant_id, endpoint, key_hash, request_id, "completed", encrypted
            )
        )
        return response
