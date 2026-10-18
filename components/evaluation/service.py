"""Evaluates stored responses; never touches generation state.

The service works on the session it is given and never commits: the entry
point (HTTP dependency or task) owns the transaction. Evaluators are pure
and fast, so reading the input, evaluating and writing the outcomes share
one session. Logs carry ids, evaluator ids and outcomes only, never response
text.
"""

import logging
import uuid

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from components.core.schemas import Principal
from components.evaluation.enums import OutcomeStatus
from components.evaluation.evaluators import EVALUATORS, build_evaluator
from components.evaluation.repository import EvaluationRepository
from components.evaluation.schemas import (
    EvaluationOutcome,
    EvaluationResultSchema,
    EvaluationSummarySchema,
    EvaluatorConfigSchema,
    EvaluatorSummarySchema,
    parse_evaluator_config,
)
from components.experiments.enums import ResultStatus
from components.experiments.exceptions import ExperimentNotFoundError
from components.experiments.repository import ExperimentRepository

logger = logging.getLogger(__name__)

# Results enqueued per backfill request; bigger backlogs are processed in
# batches by calling again.
BACKFILL_BATCH_SIZE = 1000


class EvaluationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _config(
        self,
        principal: Principal,
        experiment_id: uuid.UUID,
    ) -> EvaluatorConfigSchema:
        experiment = await ExperimentRepository(self._session).get_scoped(
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
        data = await EvaluationRepository(self._session).get_input(result_id)
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
        inserted = await EvaluationRepository(self._session).insert_ignore(rows)
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
        config = await self._config(principal, experiment_id)
        repository = EvaluationRepository(self._session)
        evaluator_ids = [e.id for e in config.evaluators]
        ids = await repository.list_unevaluated_result_ids(
            experiment_id, evaluator_ids, limit
        )
        total = await repository.count_unevaluated_results(
            experiment_id, evaluator_ids
        )
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
        experiments = ExperimentRepository(self._session)
        if await experiments.get_scoped(experiment_id, principal) is None:
            raise ExperimentNotFoundError(experiment_id)
        rows, total = await EvaluationRepository(self._session).list_page(
            experiment_id, limit, offset, evaluator_id, status
        )
        items = [EvaluationResultSchema.model_validate(r) for r in rows]
        return items, total

    async def summary(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> EvaluationSummarySchema:
        config = await self._config(principal, experiment_id)
        repository = EvaluationRepository(self._session)
        counts = await repository.outcome_counts(experiment_id)
        unevaluated = await repository.count_unevaluated_results(
            experiment_id, [e.id for e in config.evaluators]
        )
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
