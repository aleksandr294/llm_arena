"""datasets and experiments belong to an organization and an owner

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18 12:00:00.000000

Legacy rows are adopted by the organization named "Default". If one already
exists it is reused, including when it is inactive; legacy data is then
owned by an inactive organization and needs reactivation to be reachable.
Downgrade deletes the "Default" organization only if it has no users, even
when the operator created it before the upgrade.
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("datasets", "experiments")
DEFAULT_ORGANIZATION = "Default"


def upgrade() -> None:
    bind = op.get_bind()
    # Pre-existing rows have no known creator, so they go to a default
    # organization without an owner. If an organization with that name
    # already exists it is reused.
    bind.execute(
        sa.text(
            "INSERT INTO organizations (id, name, name_lower) "
            "VALUES (:id, :name, :name_lower) "
            "ON CONFLICT (name_lower) DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "name": DEFAULT_ORGANIZATION,
            "name_lower": DEFAULT_ORGANIZATION.lower(),
        },
    )
    default_id = bind.execute(
        sa.text("SELECT id FROM organizations WHERE name_lower = :n"),
        {"n": DEFAULT_ORGANIZATION.lower()},
    ).scalar_one()

    for table in TABLES:
        # Nullable first so existing rows can be backfilled, then NOT NULL.
        op.add_column(
            table, sa.Column("organization_id", sa.Uuid(), nullable=True)
        )
        backfill = f"UPDATE {table} SET organization_id = :org"  # nosec B608 - table names come from the module-level TABLES constant, never user input
        bind.execute(
            sa.text(backfill),
            {"org": default_id},
        )
        op.alter_column(table, "organization_id", nullable=False)
        op.create_foreign_key(
            f"{table}_organization_id_fkey",
            table,
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.add_column(table, sa.Column("owner_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"{table}_owner_id_fkey",
            table,
            "users",
            ["owner_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            f"ix_{table}_organization_id_created_at",
            table,
            ["organization_id", "created_at"],
            unique=False,
        )
        op.create_index(
            f"ix_{table}_owner_id_created_at",
            table,
            ["owner_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(f"ix_{table}_owner_id_created_at", table_name=table)
        op.drop_index(
            f"ix_{table}_organization_id_created_at", table_name=table
        )
        op.drop_constraint(f"{table}_owner_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "owner_id")
        op.drop_constraint(
            f"{table}_organization_id_fkey", table, type_="foreignkey"
        )
        op.drop_column(table, "organization_id")
    # Remove the default organization only if nobody belongs to it, so a
    # downgrade never orphans users.
    op.execute(
        sa.text(
            "DELETE FROM organizations WHERE name_lower = :n AND NOT EXISTS "
            "(SELECT 1 FROM users WHERE users.organization_id = "
            "organizations.id)"
        ).bindparams(n=DEFAULT_ORGANIZATION.lower())
    )
