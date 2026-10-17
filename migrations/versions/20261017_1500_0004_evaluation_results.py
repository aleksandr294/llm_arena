"""evaluation results

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17 15:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("experiment_result_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("evaluator_id", sa.String(length=64), nullable=False),
        sa.Column("evaluator_type", sa.String(length=32), nullable=False),
        sa.Column("evaluator_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["experiments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["experiment_result_id"],
            ["experiment_results.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_result_id",
            "evaluator_id",
            name="uq_evaluation_results_result_evaluator",
        ),
    )
    op.create_index(
        "ix_evaluation_results_experiment_evaluator_status",
        "evaluation_results",
        ["experiment_id", "evaluator_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evaluation_results_experiment_evaluator_status",
        table_name="evaluation_results",
    )
    op.drop_table("evaluation_results")
