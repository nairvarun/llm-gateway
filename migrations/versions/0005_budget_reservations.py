"""Atomic UTC budget buckets and attempt reservations."""

import sqlalchemy as sa
from alembic import op

revision = "0005_budget_reservations"
down_revision = "0004_replay_recovery_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("daily_budget_usd", sa.Numeric(20, 10), nullable=False, server_default="10"),
    )
    op.add_column(
        "tenants",
        sa.Column("monthly_budget_usd", sa.Numeric(20, 10), nullable=False, server_default="100"),
    )
    op.create_check_constraint(
        "ck_tenants_daily_budget_positive", "tenants", "daily_budget_usd > 0"
    )
    op.create_check_constraint(
        "ck_tenants_monthly_budget_positive", "tenants", "monthly_budget_usd > 0"
    )
    op.add_column("requests", sa.Column("max_cost_usd", sa.Numeric(20, 10)))
    op.create_table(
        "budget_buckets",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("period", sa.String(8), primary_key=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("limit_usd", sa.Numeric(20, 10), nullable=False),
        sa.Column("held_usd", sa.Numeric(20, 10), nullable=False, server_default="0"),
        sa.Column("committed_usd", sa.Numeric(20, 10), nullable=False, server_default="0"),
        sa.CheckConstraint("period IN ('day', 'month')"),
        sa.CheckConstraint("limit_usd > 0"),
        sa.CheckConstraint("held_usd >= 0"),
        sa.CheckConstraint("committed_usd >= 0"),
    )
    op.create_table(
        "spend_reservations",
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("attempts.id"), primary_key=True),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("day_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("month_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(20, 10), nullable=False),
        sa.Column("charged_usd", sa.Numeric(20, 10), nullable=False, server_default="0"),
        sa.Column("overrun_usd", sa.Numeric(20, 10), nullable=False, server_default="0"),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('held', 'reconciled', 'conservative', 'released')"),
        sa.CheckConstraint("reserved_usd >= 0"),
        sa.CheckConstraint("charged_usd >= 0"),
        sa.CheckConstraint("overrun_usd >= 0"),
    )
    op.create_index("ix_spend_reservations_request_id", "spend_reservations", ["request_id"])
    op.create_index("ix_spend_reservations_tenant_id", "spend_reservations", ["tenant_id"])


def downgrade() -> None:
    raise RuntimeError("No destructive automatic downgrade; restore or use forward migrations.")
