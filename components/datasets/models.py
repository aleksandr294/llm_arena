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


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
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
