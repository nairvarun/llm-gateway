"""Index stale ownership reconciliation without rewriting applied revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0004_replay_recovery_index"
down_revision = "0003_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attempts",
        sa.Column(
            "reserved_upper_cost_usd",
            sa.Numeric(20, 10),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "ix_idempotency_records_original_request_id",
        "idempotency_records",
        ["original_request_id"],
    )


def downgrade() -> None:
    raise RuntimeError("No destructive automatic downgrade; restore or use forward migrations.")
