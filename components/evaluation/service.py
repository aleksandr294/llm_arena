"""Evaluates stored responses; never touches generation state.

Evaluators are pure, so no transaction is open while they run: the input is
read in one short session and the outcomes are written in another. Logs
carry ids, evaluator ids and outcomes only, never response text.
"""

import logging
import uuid

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.schemas import Principal
from components.evaluation.evaluators import EVALUATORS, build_evaluator
from components.evaluation.repository import EvaluationRepository
from components.evaluation.schemas import (
    EvaluationOutcome,
    EvaluationResultSchema,
    EvaluationSummarySchema,
    EvaluatorConfigSchema,
    EvaluatorSummarySchema,
    OutcomeStatus,
    parse_evaluator_config,
)
from components.experiments.models import ResultStatus
from components.experiments.repository import ExperimentRepository
from components.experiments.service import ExperimentNotFoundError

logger = logging.getLogger(__name__)

# Results enqueued per backfill request; bigger backlogs are processed in
# batches by calling again.
BACKFILL_BATCH_SIZE = 1000


class EvaluationService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._factory = session_factory

    async def _config(
        self,
        session: AsyncSession,
        principal: Principal,
        experiment_id: uuid.UUID,
    ) -> EvaluatorConfigSchema:
        experiment = await ExperimentRepository(session).get_scoped(
            experiment_id, principal
        )
        if experiment is None:
            raise ExperimentNotFoundError(experiment_id)
        return self._parse(experiment.evaluator_config, experiment_id)

    @staticmethod
    def _parse(
        raw: dict[str, object], experiment_id: uuid.UUID
    ) -> EvaluatorConfigSchema:
        """A stored config that no longer parses (stage-3 experiments
        accepted any object) means no evaluators."""
        try:
            return parse_evaluator_config(raw)
        except ValidationError:
            logger.warning(
                "experiment %s has an unparsable evaluator config; "
                "skipping evaluation",
                experiment_id,
            )
            return EvaluatorConfigSchema()

    async def evaluate_result(self, result_id: uuid.UUID) -> int:
        """Store the outcome of every configured evaluator for one result.
        Idempotent: existing outcomes are left unchanged. Returns the number
        of rows inserted."""
        async with self._factory() as session:
            data = await EvaluationRepository(session).get_input(result_id)
            await session.rollback()
        if data is None:
            logger.info("result %s not found; nothing to evaluate", result_id)
            return 0
        if data.status != ResultStatus.SUCCEEDED.value:
            logger.info("result %s is %s; skipped", result_id, data.status)
            return 0
        config = self._parse(data.evaluator_config, data.experiment_id)
        rows = []
        for entry in config.evaluators:
            try:
                if data.response_text is None:
                    # Stored as error so the result is not pending forever.
                    outcome = EvaluationOutcome(
                        status=OutcomeStatus.ERROR,
                        details={"reason": "missing_response"},
                    )
                else:
                    outcome = build_evaluator(entry).evaluate(
                        data.response_text, data.expected_output
                    )
            except Exception as exc:
                # Only the class name: messages may echo the response.
                outcome = EvaluationOutcome(
                    status=OutcomeStatus.ERROR,
                    details={
                        "reason": "evaluator_exception",
                        "exception": type(exc).__name__,
                    },
                )
            rows.append(
                {
                    "experiment_result_id": data.result_id,
                    "experiment_id": data.experiment_id,
                    "evaluator_id": entry.id,
                    "evaluator_type": entry.type,
                    "evaluator_version": EVALUATORS[entry.type].version,
                    "status": outcome.status.value,
                    "details": outcome.details,
                }
            )
        async with self._factory() as session:
            inserted = await EvaluationRepository(session).insert_ignore(rows)
            await session.commit()
        logger.info(
            "result %s evaluated (%d evaluators, %d stored)",
            result_id,
            len(rows),
            inserted,
        )
        return inserted

    async def backfill(
        self,
        principal: Principal,
        experiment_id: uuid.UUID,
        limit: int | None = None,
    ) -> tuple[list[uuid.UUID], int]:
        """At most ``limit`` ids of succeeded results lacking some
        configured evaluation, for the caller to enqueue, and how many
        still lack one after this batch. Empty without evaluators."""
        limit = BACKFILL_BATCH_SIZE if limit is None else limit
        async with self._factory() as session:
            config = await self._config(session, principal, experiment_id)
            repository = EvaluationRepository(session)
            evaluator_ids = [e.id for e in config.evaluators]
            ids = await repository.list_unevaluated_result_ids(
                experiment_id, evaluator_ids, limit
            )
            total = await repository.count_unevaluated_results(
                experiment_id, evaluator_ids
            )
            await session.rollback()
        return ids, max(total - len(ids), 0)

    async def list_evaluations(
        self,
        principal: Principal,
        experiment_id: uuid.UUID,
        limit: int,
        offset: int,
        evaluator_id: str | None = None,
        status: str | None = None,
    ) -> tuple[list[EvaluationResultSchema], int]:
        async with self._factory() as session:
            experiments = ExperimentRepository(session)
            if await experiments.get_scoped(experiment_id, principal) is None:
                raise ExperimentNotFoundError(experiment_id)
            rows, total = await EvaluationRepository(session).list_page(
                experiment_id, limit, offset, evaluator_id, status
            )
            # Convert before the rollback, which expires the instances.
            items = [EvaluationResultSchema.model_validate(r) for r in rows]
            await session.rollback()
        return items, total

    async def summary(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> EvaluationSummarySchema:
        async with self._factory() as session:
            config = await self._config(session, principal, experiment_id)
            repository = EvaluationRepository(session)
            counts = await repository.outcome_counts(experiment_id)
            unevaluated = await repository.count_unevaluated_results(
                experiment_id, [e.id for e in config.evaluators]
            )
            await session.rollback()
        return EvaluationSummarySchema(
            evaluators=[
                EvaluatorSummarySchema(
                    id=entry.id,
                    type=entry.type,
                    version=EVALUATORS[entry.type].version,
                    **{
                        status.value: counts.get(entry.id, {}).get(
                            status.value, 0
                        )
                        for status in OutcomeStatus
                    },
                )
                for entry in config.evaluators
            ],
            unevaluated_results=unevaluated,
        )
