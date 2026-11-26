"""token revocation marker, refresh purge index and sign-in throttle

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-01 12:00:00.000000

``users.tokens_valid_after`` is nullable without a backfill: NULL means the
user never ended their access tokens. Downgrade drops the column, the index
and the throttle table; no experiment or result data is touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "tokens_valid_after", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_index(
        "ix_refresh_tokens_expires_at",
        "refresh_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "auth_throttle",
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("scope", "key_hash"),
    )


def downgrade() -> None:
    op.drop_table("auth_throttle")
    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
    op.drop_column("users", "tokens_valid_after")
