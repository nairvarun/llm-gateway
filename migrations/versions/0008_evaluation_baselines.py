"""Immutable approved evaluation baselines."""

import sqlalchemy as sa
from alembic import op

revision = "0008_evaluation_baselines"
down_revision = "0007_evaluation_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_baselines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("application_id", sa.String(100), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), sa.ForeignKey("evaluation_datasets.id"), nullable=False),
        sa.Column("profile_sha256", sa.String(64), nullable=False),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("evaluation_runs.id"), nullable=False, unique=True
        ),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("credentials.id"), nullable=False),
        sa.Column(
            "approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "application_id", "dataset_id", "profile_sha256"),
    )


def downgrade() -> None:
    raise RuntimeError("No destructive automatic downgrade; restore or use forward migrations.")
