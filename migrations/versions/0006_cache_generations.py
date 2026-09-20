"""Durable cache approval and invalidation generations."""

import sqlalchemy as sa
from alembic import op

revision = "0006_cache_generations"
down_revision = "0005_budget_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("cache_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "cache_namespaces",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("application_id", sa.String(100), primary_key=True),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("generation > 0"),
    )
    op.create_table(
        "cache_key_generations",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("application_id", sa.String(100), primary_key=True),
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("generation > 0"),
    )


def downgrade() -> None:
    raise RuntimeError("No destructive automatic downgrade; restore or use forward migrations.")
