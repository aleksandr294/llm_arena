import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from components.core.database import Base

# Registers the experiment tables the foreign keys below refer to.
from components.experiments import models as _experiments_models  # noqa: F401


class EvaluationResult(Base):
    """One evaluator outcome for one generated result. Stored apart from the
    response, which it never copies."""

    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint(
            "experiment_result_id",
            "evaluator_id",
            name="uq_evaluation_results_result_evaluator",
        ),
        Index(
            "ix_evaluation_results_experiment_evaluator_status",
            "experiment_id",
            "evaluator_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    experiment_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_results.id", ondelete="CASCADE")
    )
    # Denormalized so per-experiment summaries need no join.
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE")
    )
    evaluator_id: Mapped[str] = mapped_column(String(64))
    evaluator_type: Mapped[str] = mapped_column(String(32))
    evaluator_version: Mapped[int]
    # Lowercase OutcomeStatus value; varchar, not a PG enum type.
    status: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
