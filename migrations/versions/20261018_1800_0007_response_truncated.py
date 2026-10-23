"""flag results whose stored response was cut at the size limit

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18 18:00:00.000000

Existing rows get false: nothing stored before this revision was truncated
by the platform. Downgrade drops the column and with it the flag only; the
stored (possibly truncated) text stays.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiment_results",
        sa.Column(
            "response_truncated",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("experiment_results", "response_truncated")
