"""experiments and experiment results

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column(
            "config", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "evaluator_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "experiment_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("test_case_id", sa.Uuid(), nullable=False),
        sa.Column("case_position", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rendered_prompt", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("finish_reason", sa.String(length=64), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("reported_model", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("effective_max_tokens", sa.Integer(), nullable=True),
        sa.Column("error_kind", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["experiments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id"], ["test_cases.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id",
            "test_case_id",
            "provider",
            "model",
            name="uq_experiment_results_target",
        ),
    )
    op.create_index(
        "ix_experiment_results_experiment_status",
        "experiment_results",
        ["experiment_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_results_experiment_status",
        table_name="experiment_results",
    )
    op.drop_table("experiment_results")
    op.drop_table("experiments")
