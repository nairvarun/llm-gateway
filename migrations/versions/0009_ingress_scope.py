"""Preserve application identity for scoped replay summary accounting."""

import sqlalchemy as sa
from alembic import op

revision = "0009_ingress_scope"
down_revision = "0008_evaluation_baselines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "idempotency_ingress",
        sa.Column(
            "application_id", sa.String(100), nullable=False, server_default="legacy-unknown"
        ),
    )
    op.create_index(
        "ix_idempotency_ingress_scope_time",
        "idempotency_ingress",
        ["tenant_id", "application_id", "created_at"],
    )


def downgrade() -> None:
    raise RuntimeError("No destructive automatic downgrade; restore or use forward migrations.")
