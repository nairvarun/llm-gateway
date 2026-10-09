"""/admin: create and revoke virtual keys, read usage. Never routed through the Ingress."""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from gateway.errors import BadRequest, Forbidden, NotFound, Unauthorized

if TYPE_CHECKING:
    from gateway.app import Deps


class CreateKey(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    allowed_models: list[str] | None = None
    rpm_limit: int | None = Field(None, gt=0)
    monthly_budget_usd: float | None = Field(None, ge=0)


def bearer_from(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def make_admin_router(deps: Deps) -> APIRouter:
    def require_admin(request: Request) -> None:
        token = bearer_from(request)
        if token is None:
            raise Unauthorized("missing admin token")
        if token.startswith("gw-"):
            raise Forbidden("virtual keys cannot use the admin API")
        if not hmac.compare_digest(token.encode(), deps.settings.admin_token.encode()):
            raise Unauthorized("invalid admin token")

    router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

    @router.post("/keys", status_code=201)
    async def create_key(body: CreateKey) -> dict:
        unknown = set(body.allowed_models or []) - set(deps.cfg.models)
        if unknown:
            raise BadRequest(f"unknown models in allowed_models: {sorted(unknown)}")
        key, plaintext = await deps.store.create_key(
            body.name, body.allowed_models, body.rpm_limit, body.monthly_budget_usd
        )
        return {
            "id": key.id,
            "key": plaintext,  # shown once, never stored or logged
            "name": key.name,
            "allowed_models": key.allowed_models,
            "rpm_limit": key.rpm_limit,
            "monthly_budget_usd": key.monthly_budget_usd,
            "created_at": key.created_at,
        }

    @router.get("/keys/{key_id}/usage")
    async def key_usage(key_id: str) -> dict:
        if await deps.store.get_key(key_id) is None:
            raise NotFound(f"no key {key_id!r}")
        return await deps.store.usage_summary(key_id)

    @router.delete("/keys/{key_id}", status_code=204)
    async def revoke_key(key_id: str) -> Response:
        if not await deps.store.revoke_key(key_id):
            raise NotFound(f"no active key {key_id!r}")
        return Response(status_code=204)

    return router
