import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update

from components.core.access import scope_clause
from components.core.repository import BaseRepository
from components.core.schemas import Principal
from components.experiments.logic import derive_final_status
from components.experiments.models import (
    Experiment,
    ExperimentResult,
    ResultStatus,
)

# A result "running" for longer than this is assumed to belong to a crashed
# worker and may be claimed again. Must exceed max attempts (5) * timeout
# (capped at 120 s) plus backoff (30 s cap per retry) plus time spent
# waiting for a provider semaphore: 5 * 120 + 4 * 30 = 720 s, leaving
# headroom up to 1800 s.
LEASE_SECONDS = 1800
INSERT_BATCH_SIZE = 500
_USE_SCOPED = "use the scoped method"


class ExperimentRepository(BaseRepository[Experiment]):
    """Scoped methods take a ``Principal`` and treat out-of-scope rows as
    missing. The ``*_unscoped`` methods are for workers and the runner,
    which have no user context; request handlers must not call them. The
    inherited unscoped ``get``/``delete``/``list`` are disabled."""

    model = Experiment

    async def get(self, entity_id: Any) -> Experiment | None:
        raise NotImplementedError(_USE_SCOPED)

    async def delete(self, entity: Experiment) -> None:
        raise NotImplementedError(_USE_SCOPED)

    async def list(
        self, limit: int = 100, offset: int = 0
    ) -> Sequence[Experiment]:
        raise NotImplementedError(_USE_SCOPED)

    async def list_page(
        self,
        principal: Principal,
        limit: int,
        offset: int,
        status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> tuple[Sequence[Experiment], int]:
        """Return one visible page ordered by ``(created_at, id)`` and the
        total of visible experiments matching the filters (bounds
        inclusive). ``owner_id`` only narrows the scope."""
        conditions = [scope_clause(Experiment, principal)]
        if status is not None:
            conditions.append(Experiment.status == status)
        if created_from is not None:
            conditions.append(Experiment.created_at >= created_from)
        if created_to is not None:
            conditions.append(Experiment.created_at <= created_to)
        if owner_id is not None:
            conditions.append(Experiment.owner_id == owner_id)
        total = await self.session.scalar(
            select(func.count()).select_from(Experiment).where(*conditions)
        )
        result = await self.session.scalars(
            select(Experiment)
            .where(*conditions)
            .order_by(Experiment.created_at, Experiment.id)
            .limit(limit)
            .offset(offset)
        )
        return result.all(), total or 0

    async def get_scoped(
        self, experiment_id: uuid.UUID, principal: Principal
    ) -> Experiment | None:
        return await self.session.scalar(
            select(Experiment).where(
                Experiment.id == experiment_id,
                scope_clause(Experiment, principal),
            )
        )

    async def get_for_update(
        self, experiment_id: uuid.UUID, principal: Principal
    ) -> Experiment | None:
        """Lock a visible experiment row. Every writer that changes
        experiment or result state locks this row first, which avoids
        deadlocks."""
        return await self.session.scalar(
            select(Experiment)
            .where(
                Experiment.id == experiment_id,
                scope_clause(Experiment, principal),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def get_for_update_unscoped(
        self, experiment_id: uuid.UUID
    ) -> Experiment | None:
        """Like ``get_for_update`` but ignoring ownership (runner)."""
        return await self.session.scalar(
            select(Experiment)
            .where(Experiment.id == experiment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )


class ExperimentResultRepository(BaseRepository[ExperimentResult]):
    model = ExperimentResult

    async def add_many(self, results: Sequence[ExperimentResult]) -> None:
        """Insert results in batches to bound statement size."""
        for start in range(0, len(results), INSERT_BATCH_SIZE):
            self.session.add_all(results[start : start + INSERT_BATCH_SIZE])
            await self.session.flush()

    async def list_page(
        self,
        experiment_id: uuid.UUID,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[Sequence[ExperimentResult], int]:
        """Return one page ordered by case position then target, and the
        total matching the optional status filter."""
        conditions = [ExperimentResult.experiment_id == experiment_id]
        if status is not None:
            conditions.append(ExperimentResult.status == status)
        total = await self.session.scalar(
            select(func.count())
            .select_from(ExperimentResult)
            .where(*conditions)
        )
        result = await self.session.scalars(
            select(ExperimentResult)
            .where(*conditions)
            .order_by(
                ExperimentResult.case_position,
                ExperimentResult.provider,
                ExperimentResult.model,
                ExperimentResult.id,
            )
            .limit(limit)
            .offset(offset)
        )
        return result.all(), total or 0

    async def status_counts(self, experiment_id: uuid.UUID) -> dict[str, int]:
        """Result count per status; statuses without results are absent."""
        rows = await self.session.execute(
            select(ExperimentResult.status, func.count())
            .where(ExperimentResult.experiment_id == experiment_id)
            .group_by(ExperimentResult.status)
        )
        return {status: count for status, count in rows.all()}

    async def list_ids_by_status(
        self, experiment_id: uuid.UUID, status: ResultStatus
    ) -> list[uuid.UUID]:
        """Ids of an experiment's results in ``status``, in case order."""
        rows = await self.session.scalars(
            select(ExperimentResult.id)
            .where(
                ExperimentResult.experiment_id == experiment_id,
                ExperimentResult.status == status.value,
            )
            .order_by(
                ExperimentResult.case_position,
                ExperimentResult.provider,
                ExperimentResult.model,
                ExperimentResult.id,
            )
        )
        return list(rows.all())

    async def list_stale_running_ids(
        self,
        experiment_id: uuid.UUID,
        lease_seconds: int = LEASE_SECONDS,
    ) -> list[uuid.UUID]:
        """Ids of running results whose lease expired (crashed worker), in
        case order."""
        rows = await self.session.scalars(
            select(ExperimentResult.id)
            .where(
                ExperimentResult.experiment_id == experiment_id,
                ExperimentResult.status == ResultStatus.RUNNING.value,
                ExperimentResult.started_at
                < func.now() - timedelta(seconds=lease_seconds),
            )
            .order_by(
                ExperimentResult.case_position,
                ExperimentResult.provider,
                ExperimentResult.model,
                ExperimentResult.id,
            )
        )
        return list(rows.all())

    async def cancel_pending(self, experiment_id: uuid.UUID) -> int:
        """Mark pending results cancelled; running ones are left alone.
        Returns the number of cancelled results."""
        rows = await self.session.scalars(
            update(ExperimentResult)
            .where(
                ExperimentResult.experiment_id == experiment_id,
                ExperimentResult.status == ResultStatus.PENDING.value,
            )
            .values(status=ResultStatus.CANCELLED.value, finished_at=func.now())
            .returning(ExperimentResult.id)
        )
        return len(rows.all())

    async def reset_failed(self, experiment_id: uuid.UUID) -> list[uuid.UUID]:
        """Move failed results back to pending for another run.

        The previous outcome (error, attempts and timestamps) is cleared so
        the row describes the new run only; succeeded results are untouched.
        """
        rows = await self.session.scalars(
            update(ExperimentResult)
            .where(
                ExperimentResult.experiment_id == experiment_id,
                ExperimentResult.status == ResultStatus.FAILED.value,
            )
            .values(
                status=ResultStatus.PENDING.value,
                attempts=0,
                error_kind=None,
                error_message=None,
                provider_request_id=None,
                started_at=None,
                finished_at=None,
            )
            .returning(ExperimentResult.id)
        )
        return list(rows.all())

    async def count_non_terminal(self, experiment_id: uuid.UUID) -> int:
        """Count pending and running results."""
        total = await self.session.scalar(
            select(func.count())
            .select_from(ExperimentResult)
            .where(
                ExperimentResult.experiment_id == experiment_id,
                ExperimentResult.status.in_(
                    [ResultStatus.PENDING.value, ResultStatus.RUNNING.value]
                ),
            )
        )
        return total or 0

    async def claim(
        self, result_id: uuid.UUID, lease_seconds: int = LEASE_SECONDS
    ) -> ExperimentResult | None:
        """Atomically move a pending result (or a running one whose lease
        expired) to running and count the attempt.

        Returns the claimed row (its ``started_at`` identifies this claim
        and must be passed to ``write_outcome``), or ``None`` when another
        worker owns it or it is already finished. The caller must commit
        before calling the model so the claim is visible to concurrent
        claimers.
        """
        running = ResultStatus.RUNNING.value
        stmt = (
            update(ExperimentResult)
            .where(
                ExperimentResult.id == result_id,
                (ExperimentResult.status == ResultStatus.PENDING.value)
                | (
                    (ExperimentResult.status == running)
                    & (
                        ExperimentResult.started_at
                        < func.now() - timedelta(seconds=lease_seconds)
                    )
                ),
            )
            .values(
                status=running,
                attempts=ExperimentResult.attempts + 1,
                started_at=func.now(),
            )
            .returning(ExperimentResult)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(stmt)

    async def write_outcome(
        self,
        result_id: uuid.UUID,
        status: ResultStatus,
        fields: Mapping[str, Any],
        claimed_started_at: datetime,
    ) -> bool:
        """Store a terminal outcome only while the result is still running
        under the caller's claim (same ``started_at``), so a worker whose
        lease was reclaimed cannot write over the new owner or its outcome.
        Returns whether the row was updated."""
        stmt = (
            update(ExperimentResult)
            .where(
                ExperimentResult.id == result_id,
                ExperimentResult.status == ResultStatus.RUNNING.value,
                ExperimentResult.started_at == claimed_started_at,
            )
            .values(**fields, status=status.value, finished_at=func.now())
            .returning(ExperimentResult.id)
        )
        return await self.session.scalar(stmt) is not None


async def finalize_experiment(
    experiment: Experiment, results: ExperimentResultRepository
) -> bool:
    """Set the final status once no result is pending or running.

    The caller must hold the experiment row lock. Returns whether the
    experiment is now in a final status.
    """
    final = derive_final_status(
        await results.status_counts(experiment.id), experiment.cancel_requested
    )
    if final is None:
        return False
    experiment.status = final.value
    if experiment.finished_at is None:
        experiment.finished_at = datetime.now(UTC)
    await results.session.flush()
    return True
