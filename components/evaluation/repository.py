import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert

from components.core.repository import BaseRepository
from components.datasets.models import TestCase
from components.evaluation.models import EvaluationResult
from components.evaluation.schemas import EvaluationInput
from components.experiments.enums import ResultStatus
from components.experiments.models import Experiment, ExperimentResult


class EvaluationRepository(BaseRepository[EvaluationResult]):
    model = EvaluationResult

    async def insert_ignore(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Insert evaluation rows, leaving existing
        ``(experiment_result_id, evaluator_id)`` pairs untouched. Each row
        maps column names to values (no ``id``). Returns the number of rows
        actually inserted."""
        if not rows:
            return 0
        stmt = (
            insert(EvaluationResult)
            .values([{"id": uuid.uuid4(), **row} for row in rows])
            .on_conflict_do_nothing(
                constraint="uq_evaluation_results_result_evaluator"
            )
            .returning(EvaluationResult.id)
        )
        inserted = await self.session.scalars(stmt)
        return len(inserted.all())

    async def get_input(self, result_id: uuid.UUID) -> EvaluationInput | None:
        """Read a result with its case's input and expected output and its
        experiment's frozen evaluator configuration."""
        row = (
            await self.session.execute(
                select(
                    ExperimentResult.id,
                    ExperimentResult.experiment_id,
                    ExperimentResult.status,
                    ExperimentResult.response_text,
                    TestCase.input,
                    TestCase.expected_output,
                    Experiment.evaluator_config,
                )
                .join(TestCase, TestCase.id == ExperimentResult.test_case_id)
                .join(
                    Experiment, Experiment.id == ExperimentResult.experiment_id
                )
                .where(ExperimentResult.id == result_id)
            )
        ).first()
        if row is None:
            return None
        return EvaluationInput(*row)

    async def list_page(
        self,
        experiment_id: uuid.UUID,
        limit: int,
        offset: int,
        evaluator_id: str | None = None,
        status: str | None = None,
    ) -> tuple[Sequence[EvaluationResult], int]:
        """Return one page ordered by case position, target and evaluator,
        and the total matching the optional filters."""
        conditions = [EvaluationResult.experiment_id == experiment_id]
        if evaluator_id is not None:
            conditions.append(EvaluationResult.evaluator_id == evaluator_id)
        if status is not None:
            conditions.append(EvaluationResult.status == status)
        total = await self.session.scalar(
            select(func.count())
            .select_from(EvaluationResult)
            .where(*conditions)
        )
        rows = await self.session.scalars(
            select(EvaluationResult)
            .join(
                ExperimentResult,
                ExperimentResult.id == EvaluationResult.experiment_result_id,
            )
            .where(*conditions)
            .order_by(
                ExperimentResult.case_position,
                ExperimentResult.provider,
                ExperimentResult.model,
                EvaluationResult.evaluator_id,
                EvaluationResult.id,
            )
            .limit(limit)
            .offset(offset)
        )
        return rows.all(), total or 0

    async def outcome_counts(
        self, experiment_id: uuid.UUID
    ) -> dict[str, dict[str, int]]:
        """Outcome counts per evaluator id; absent keys mean zero."""
        rows = await self.session.execute(
            select(
                EvaluationResult.evaluator_id,
                EvaluationResult.status,
                func.count(),
            )
            .where(EvaluationResult.experiment_id == experiment_id)
            .group_by(EvaluationResult.evaluator_id, EvaluationResult.status)
        )
        counts: dict[str, dict[str, int]] = {}
        for evaluator_id, status, count in rows.all():
            counts.setdefault(evaluator_id, {})[status] = count
        return counts

    @staticmethod
    def _unevaluated_query(
        experiment_id: uuid.UUID, wanted: set[str]
    ) -> Select[Any]:
        stored = func.count(func.distinct(EvaluationResult.evaluator_id))
        return (
            select(ExperimentResult.id)
            .outerjoin(
                EvaluationResult,
                (EvaluationResult.experiment_result_id == ExperimentResult.id)
                & EvaluationResult.evaluator_id.in_(wanted),
            )
            .where(
                ExperimentResult.experiment_id == experiment_id,
                ExperimentResult.status == ResultStatus.SUCCEEDED.value,
            )
            .group_by(
                ExperimentResult.id,
                ExperimentResult.case_position,
                ExperimentResult.provider,
                ExperimentResult.model,
            )
            .having(stored < len(wanted))
        )

    async def list_unevaluated_result_ids(
        self,
        experiment_id: uuid.UUID,
        evaluator_ids: Sequence[str],
        limit: int | None = None,
    ) -> list[uuid.UUID]:
        """Ids of succeeded results lacking an evaluation row for at least
        one of the configured ``evaluator_ids``, in case order (at most
        ``limit``). Rows of evaluators no longer configured are ignored."""
        wanted = set(evaluator_ids)
        if not wanted:
            return []
        query = self._unevaluated_query(experiment_id, wanted).order_by(
            ExperimentResult.case_position,
            ExperimentResult.provider,
            ExperimentResult.model,
            ExperimentResult.id,
        )
        if limit is not None:
            query = query.limit(limit)
        return list((await self.session.scalars(query)).all())

    async def count_unevaluated_results(
        self, experiment_id: uuid.UUID, evaluator_ids: Sequence[str]
    ) -> int:
        """Same set as ``list_unevaluated_result_ids``, counted in the
        database without loading ids."""
        wanted = set(evaluator_ids)
        if not wanted:
            return 0
        subquery = self._unevaluated_query(experiment_id, wanted).subquery()
        count = await self.session.scalar(
            select(func.count()).select_from(subquery)
        )
        return count or 0
