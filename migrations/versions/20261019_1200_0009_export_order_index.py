"""index experiment_results for the export ordering

Revision ID: 0009
Revises: 0007
Create Date: 2026-10-19 12:00:00.000000

Index only, no data change. Re-parent onto 0008 once the auth hardening
change has merged. CREATE INDEX blocks writes to experiment_results while it
runs: migrate between experiment runs.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_experiment_results_export_order",
        "experiment_results",
        ["experiment_id", "case_position", "provider", "model", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_results_export_order", table_name="experiment_results"
    )
