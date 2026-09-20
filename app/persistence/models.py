from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint("status IN ('active', 'disabled')"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="active")


class Credential(Base):
    __tablename__ = "credentials"
    __table_args__ = (CheckConstraint("role IN ('tenant', 'operator')"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    application_id: Mapped[str] = mapped_column(String(100))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(16), default="tenant")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConfigurationVersion(Base):
    __tablename__ = "configuration_versions"
    __table_args__ = (UniqueConstraint("kind", "name", "version"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(100))
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RoutingControl(Base):
    __tablename__ = "routing_control"
    id: Mapped[int] = mapped_column(primary_key=True)
    active_policy_id: Mapped[UUID] = mapped_column(ForeignKey("configuration_versions.id"))
    disabled_providers: Mapped[list[str]] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(default=1)


class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "version"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(100))
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RequestRecord(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint("status IN ('in_progress', 'completed', 'failed', 'uncertain')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    application_id: Mapped[str] = mapped_column(String(100))
    endpoint: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(16), default="in_progress")
    input_hash: Mapped[str] = mapped_column(String(64))
    schema_hash: Mapped[str | None] = mapped_column(String(64))
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("configuration_versions.id"))
    policy_version: Mapped[str] = mapped_column(String(100))
    routing_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("request_id", "number"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    request_id: Mapped[UUID] = mapped_column(ForeignKey("requests.id"), index=True)
    number: Mapped[int] = mapped_column(default=1)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    model_id: Mapped[UUID] = mapped_column(ForeignKey("configuration_versions.id"))
    pricing_id: Mapped[UUID] = mapped_column(ForeignKey("configuration_versions.id"))
    reserved_upper_cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), default=0)
    outcome: Mapped[str] = mapped_column(String(16), default="dispatched")
    finish_reason: Mapped[str | None] = mapped_column(String(16))
    error_class: Mapped[str | None] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint("input_tokens IS NULL OR input_tokens >= 0"),
        CheckConstraint("output_tokens IS NULL OR output_tokens >= 0"),
        CheckConstraint("estimated_cost_usd >= 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("attempts.id"), unique=True)
    request_id: Mapped[UUID] = mapped_column(ForeignKey("requests.id"))
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    pricing_id: Mapped[UUID] = mapped_column(ForeignKey("configuration_versions.id"))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    usage_status: Mapped[str] = mapped_column(String(16))
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        CheckConstraint("status IN ('in_progress', 'completed', 'failed', 'uncertain')"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(50), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    original_request_id: Mapped[UUID] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(16))
    encrypted_result: Mapped[bytes | None]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    owner_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyIngress(Base):
    __tablename__ = "idempotency_ingress"
    ingress_request_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    endpoint: Mapped[str] = mapped_column(String(50))
    original_request_id: Mapped[UUID]
    outcome: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("credentials.id"))
    action: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
