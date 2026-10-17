import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from components.core.database import Base

# The foreign keys below need these tables in the metadata even when only
# this module is imported.
from components.organizations import models as _organizations  # noqa: F401
from components.users import models as _users  # noqa: F401


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        Index(
            "ix_datasets_organization_id_created_at",
            "organization_id",
            "created_at",
        ),
        Index("ix_datasets_owner_id_created_at", "owner_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # RESTRICT: organizations and users are deactivated, never deleted.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    # NULL for datasets that predate ownership (legacy rows).
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TestCase(Base):
    __tablename__ = "test_cases"
    __test__ = False  # not a pytest test class
    __table_args__ = (
        UniqueConstraint(
            "dataset_id", "position", name="uq_test_cases_position"
        ),
        Index("ix_test_cases_dataset_id", "dataset_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text, default=None)
    # "metadata" is reserved on declarative models, hence the attribute name.
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
