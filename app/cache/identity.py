"""Exact identity without prompt normalization or sensitive plaintext keys."""

import hashlib
import hmac
import json
from dataclasses import dataclass

from app.api.schemas import GenerateRequest
from app.domain.models import JSONSchema, Principal
from app.domain.routing import RegistrySnapshot


@dataclass(frozen=True)
class CacheEligibility:
    eligible: bool
    reason: str


def eligibility(request: GenerateRequest, application_approved: bool) -> CacheEligibility:
    if request.cache_mode == "bypass":
        return CacheEligibility(False, "client_bypass")
    if not application_approved:
        return CacheEligibility(False, "application_not_approved")
    if request.cache_classification != "approved_non_sensitive":
        return CacheEligibility(False, "sensitive_or_unclassified")
    if request.temperature != 0:
        return CacheEligibility(False, "nondeterministic_sampling")
    return CacheEligibility(True, "eligible")


def exact_identity(
    hash_key: bytes,
    principal: Principal,
    endpoint: str,
    request: GenerateRequest,
    schema: JSONSchema | None,
    registry: RegistrySnapshot,
) -> str:
    fields = request.model_dump(mode="json", exclude={"idempotency_key", "cache_mode"})
    content = {
        "tenant_id": str(principal.tenant_id),
        "application_id": principal.application_id,
        "endpoint": endpoint,
        "fields": fields,
        "resolved_schema": schema,
        "policy_id": str(registry.policy_id),
        "policy_version": registry.policy_version,
        "models": [
            [str(model.id), model.version, str(model.pricing_id), model.pricing_version]
            for model in registry.models
        ],
    }
    serialized = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(hash_key, serialized.encode(), hashlib.sha256).hexdigest()
