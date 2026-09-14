"""Foundation critical-state tables and immutable configuration/schema versions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Freeze the initial schema here. Importing live ORM metadata would let a
    # later model edit silently rewrite what this historical revision creates.
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled')"),
    )
    op.create_table(
        "credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("application_id", sa.String(100), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("role IN ('tenant', 'operator')"),
    )
    op.create_table(
        "configuration_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.UniqueConstraint("kind", "name", "version"),
    )
    op.create_table(
        "schema_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", "version"),
    )
    op.create_table(
        "requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("application_id", sa.String(100), nullable=False),
        sa.Column("endpoint", sa.String(50), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("schema_hash", sa.String(64)),
        sa.Column(
            "policy_id", sa.Uuid(), sa.ForeignKey("configuration_versions.id"), nullable=False
        ),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("error_code", sa.String(50)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('in_progress', 'completed', 'failed', 'uncertain')"),
    )
    op.create_table(
        "attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column(
            "model_id", sa.Uuid(), sa.ForeignKey("configuration_versions.id"), nullable=False
        ),
        sa.Column(
            "pricing_id", sa.Uuid(), sa.ForeignKey("configuration_versions.id"), nullable=False
        ),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("finish_reason", sa.String(16)),
        sa.Column("error_class", sa.String(50)),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("request_id", "number"),
    )
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "attempt_id", sa.Uuid(), sa.ForeignKey("attempts.id"), nullable=False, unique=True
        ),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "pricing_id", sa.Uuid(), sa.ForeignKey("configuration_versions.id"), nullable=False
        ),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("usage_status", sa.String(16), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(20, 10), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("input_tokens IS NULL OR input_tokens >= 0"),
        sa.CheckConstraint("output_tokens IS NULL OR output_tokens >= 0"),
        sa.CheckConstraint("estimated_cost_usd >= 0"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("credentials.id")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    for table, column in (
        ("credentials", "tenant_id"),
        ("requests", "tenant_id"),
        ("attempts", "request_id"),
        ("usage_events", "tenant_id"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])
    op.execute("""
        CREATE FUNCTION prevent_version_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Version records are immutable';
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in ("configuration_versions", "schema_versions"):
        op.execute(f"""
            CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_version_mutation();
        """)


def downgrade() -> None:
    raise RuntimeError("No destructive automatic downgrade; restore or use forward migrations.")
