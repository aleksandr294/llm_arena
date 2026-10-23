import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from components.core.database import Base

# The foreign keys below need the datasets tables in the metadata even when
# only this module is imported (e.g. a worker that loads just
# ``experiments.tasks``), or SQLAlchemy cannot resolve them on flush.
from components.datasets import models as _datasets_models  # noqa: F401
from components.experiments.enums import ExperimentStatus, ResultStatus


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        Index(
            "ix_experiments_organization_id_created_at",
            "organization_id",
            "created_at",
        ),
        Index("ix_experiments_owner_id_created_at", "owner_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    # RESTRICT: a dataset referenced by an experiment cannot be deleted.
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT")
    )
    # RESTRICT: organizations and users are deactivated, never deleted.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    # NULL for experiments that predate ownership (legacy rows).
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), default=None
    )
    # Lowercase ExperimentStatus value; varchar, not a PG enum type.
    status: Mapped[str] = mapped_column(
        String(32), default=ExperimentStatus.PENDING.value
    )
    prompt_template: Mapped[str] = mapped_column(Text)
    # targets, system_prompt, temperature, max_tokens, timeout_seconds,
    # max_attempts.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evaluator_config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class ExperimentResult(Base):
    __tablename__ = "experiment_results"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "test_case_id",
            "provider",
            "model",
            name="uq_experiment_results_target",
        ),
        Index(
            "ix_experiment_results_experiment_status", "experiment_id", "status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE")
    )
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT")
    )
    # Copied from the case at creation so ordering needs no join.
    case_position: Mapped[int]
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(32), default=ResultStatus.PENDING.value
    )
    # Exact prompt, rendered at creation and frozen.
    rendered_prompt: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    response_text: Mapped[str | None] = mapped_column(Text, default=None)
    # True when response_text was cut at MAX_STORED_RESPONSE_CHARS.
    response_truncated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    finish_reason: Mapped[str | None] = mapped_column(String(64), default=None)
    provider_request_id: Mapped[str | None] = mapped_column(
        String(255), default=None
    )
    reported_model: Mapped[str | None] = mapped_column(
        String(255), default=None
    )
    # Token counts and latency are measured values; NULL means unknown.
    input_tokens: Mapped[int | None] = mapped_column(default=None)
    output_tokens: Mapped[int | None] = mapped_column(default=None)
    total_tokens: Mapped[int | None] = mapped_column(default=None)
    latency_ms: Mapped[float | None] = mapped_column(Float, default=None)
    effective_max_tokens: Mapped[int | None] = mapped_column(default=None)
    error_kind: Mapped[str | None] = mapped_column(String(64), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    # started_at doubles as the lease timestamp of the current claim.
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
