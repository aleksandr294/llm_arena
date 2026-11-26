"""index the foreign keys used to find experiments and results of a case

Revision ID: 0010
Revises: 0007
Create Date: 2026-11-08 22:00:00.000000

``experiments.dataset_id`` and ``experiment_results.test_case_id`` are
looked up (and checked by the RESTRICT foreign keys on delete) without an
index. No data changes; downgrade drops the indexes. Creating them takes a
short write lock, so apply between experiment runs.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_experiments_dataset_id", "experiments", ["dataset_id"])
    op.create_index(
        "ix_experiment_results_test_case_id",
        "experiment_results",
        ["test_case_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_results_test_case_id", table_name="experiment_results"
    )
    op.drop_index("ix_experiments_dataset_id", table_name="experiments")
