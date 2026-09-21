"""Durable evaluation datasets, runs, cases, and traffic attribution."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_evaluation_runs"
down_revision = "0006_cache_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column("traffic_kind", sa.String(16), nullable=False, server_default="application"),
    )
    op.add_column("requests", sa.Column("evaluation_run_id", sa.Uuid(), nullable=True))
    op.create_index("ix_requests_evaluation_run_id", "requests", ["evaluation_run_id"])
    op.create_check_constraint(
        "ck_requests_traffic_kind", "requests", "traffic_kind IN ('application', 'evaluation')"
    )
    op.create_table(
        "evaluation_datasets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.String(200), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("name", "version"),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("application_id", sa.String(100), nullable=False),
        sa.Column("credential_id", sa.Uuid(), sa.ForeignKey("credentials.id"), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), sa.ForeignKey("evaluation_datasets.id"), nullable=False),
        sa.Column("dataset_sha256", sa.String(64), nullable=False),
        sa.Column("model_ids", JSONB(), nullable=False),
        sa.Column(
            "policy_id", sa.Uuid(), sa.ForeignKey("configuration_versions.id"), nullable=False
        ),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("threshold_profile", sa.String(100), nullable=False),
        sa.Column("threshold_sha256", sa.String(64), nullable=False),
        sa.Column("code_revision", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("evaluator_version", sa.String(100), nullable=False),
        sa.Column("pricing_versions", JSONB(), nullable=False),
        sa.Column("sampling_settings", JSONB(), nullable=False),
        sa.Column("model_revision_limitations", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(50)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted')"
        ),
    )
    op.create_index("ix_evaluation_runs_tenant_id", "evaluation_runs", ["tenant_id"])
    op.create_table(
        "evaluation_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("evaluation_runs.id"), nullable=False),
        sa.Column("case_id", sa.String(64), nullable=False),
        sa.Column("case_sha256", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_id", sa.Uuid(), unique=True),
        sa.Column("error_code", sa.String(50)),
        sa.Column("score", JSONB()),
        sa.Column("latency_ms", sa.Numeric(20, 3)),
        sa.Column("estimated_cost_usd", sa.Numeric(20, 10)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "case_id", "model_id"),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'uncertain')"),
    )
    op.create_index("ix_evaluation_cases_run_id", "evaluation_cases", ["run_id"])
    op.create_foreign_key(
        "fk_requests_evaluation_run_id",
        "requests",
        "evaluation_runs",
        ["evaluation_run_id"],
        ["id"],
    )


def downgrade() -> None:
    raise RuntimeError("No destructive automatic downgrade; restore or use forward migrations.")
