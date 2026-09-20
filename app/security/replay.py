"""Keyed identity and authenticated encrypted terminal replay envelopes."""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.api.schemas import GenerateRequest
from app.domain.models import JSONSchema, Principal, StateUnavailable


class ReplayCipher:
    def __init__(self, base64_key: str) -> None:
        try:
            key = base64.b64decode(base64_key, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Invalid replay encryption key") from error
        if len(key) != 32:
            raise ValueError("Replay encryption key must be 32 random bytes, base64 encoded")
        self._key = key
        self._cipher = AESGCM(key)

    def key_hash(self, tenant_id: UUID, endpoint: str, literal_key: str) -> str:
        return self._hash(f"key:{tenant_id}:{endpoint}:{literal_key}".encode())

    def fingerprint(
        self,
        principal: Principal,
        endpoint: str,
        request: GenerateRequest,
        schema: JSONSchema | None,
    ) -> str:
        fields = request.model_dump(mode="json", exclude={"idempotency_key"})
        payload = {
            "application_id": principal.application_id,
            "endpoint": endpoint,
            "fields": fields,
            "resolved_schema": schema,
        }
        return self._hash(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())

    def _hash(self, content: bytes) -> str:
        return hmac.new(self._key, content, hashlib.sha256).hexdigest()

    @staticmethod
    def _aad(tenant_id: UUID, endpoint: str, key_hash: str, original: UUID) -> bytes:
        return f"{tenant_id}:{endpoint}:{key_hash}:{original}".encode()

    def seal(
        self,
        tenant_id: UUID,
        endpoint: str,
        key_hash: str,
        original: UUID,
        payload: dict[str, Any],
    ) -> bytes:
        nonce = secrets.token_bytes(12)
        content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return (
            b"\x01"
            + nonce
            + self._cipher.encrypt(
                nonce, content, self._aad(tenant_id, endpoint, key_hash, original)
            )
        )

    def open(
        self, tenant_id: UUID, endpoint: str, key_hash: str, original: UUID, envelope: bytes
    ) -> dict[str, Any]:
        if len(envelope) < 30 or envelope[0] != 1:
            raise StateUnavailable()
        try:
            decoded = json.loads(
                self._cipher.decrypt(
                    envelope[1:13],
                    envelope[13:],
                    self._aad(tenant_id, endpoint, key_hash, original),
                )
            )
        except (InvalidTag, ValueError, UnicodeDecodeError, TypeError) as error:
            raise StateUnavailable() from error
        if not isinstance(decoded, dict):
            raise StateUnavailable()
        return decoded
