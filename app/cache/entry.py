"""Authenticated encrypted result envelopes with independent logical expiry."""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.models import StateUnavailable
from app.security.replay import ReplayCipher


@dataclass(frozen=True)
class CacheEntry:
    source_request_id: UUID
    response: dict[str, Any]
    created_at: datetime
    expires_at: datetime


def seal_entry(
    cipher: ReplayCipher,
    tenant_id: UUID,
    endpoint: str,
    key_hash: str,
    source_request_id: UUID,
    response: dict[str, Any],
    ttl_seconds: int,
) -> bytes:
    created = datetime.now(UTC)
    expires = created + timedelta(seconds=ttl_seconds)
    protected = cipher.seal(
        tenant_id,
        endpoint,
        key_hash,
        source_request_id,
        {
            "response": response,
            "created_at": created.isoformat(),
            "expires_at": expires.isoformat(),
        },
    )
    return json.dumps(
        {
            "source_request_id": str(source_request_id),
            "envelope": base64.b64encode(protected).decode(),
        },
        separators=(",", ":"),
    ).encode()


def open_entry(
    cipher: ReplayCipher,
    tenant_id: UUID,
    endpoint: str,
    key_hash: str,
    serialized: bytes,
    *,
    now: datetime | None = None,
) -> CacheEntry | None:
    try:
        outer = json.loads(serialized)
        source = UUID(outer["source_request_id"])
        encrypted = base64.b64decode(outer["envelope"], validate=True)
        content = cipher.open(tenant_id, endpoint, key_hash, source, encrypted)
        created = datetime.fromisoformat(content["created_at"])
        expires = datetime.fromisoformat(content["expires_at"])
        response = content["response"]
        if (
            not isinstance(response, dict)
            or created.tzinfo is None
            or expires.tzinfo is None
            or expires <= (now or datetime.now(UTC))
            or expires <= created
        ):
            return None
        return CacheEntry(source, response, created, expires)
    except (
        StateUnavailable,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        UnicodeDecodeError,
        binascii.Error,
    ):
        return None
