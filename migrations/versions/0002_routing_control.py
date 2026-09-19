"""Active routing pointer and provider kill switch; versions stay immutable."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_routing_control"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("requests", sa.Column("routing_evidence", JSONB()))
    op.create_table(
        "routing_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "active_policy_id",
            sa.Uuid(),
            sa.ForeignKey("configuration_versions.id"),
            nullable=False,
        ),
        sa.Column("disabled_providers", JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1"),
        sa.CheckConstraint("revision > 0"),
    )


def downgrade() -> None:
    raise RuntimeError("No destructive automatic downgrade; restore or use forward migrations.")
