"""Evaluates stored responses; never touches generation state.

Deterministic evaluators are pure and fast, so reading the input,
evaluating and writing their outcomes share one short transaction opened
through ``scope``. An ``llm_judge`` evaluator is neither: its provider call
is made with no session open, exactly like ``ExperimentRunner``'s model
call, and its outcome is persisted in a second, separate transaction, also
opened through ``scope``. ``evaluate_result`` therefore needs ``scope`` (a
``SessionScope``), not a single injected session; the read-only HTTP-facing
methods below it keep the single-session-per-request pattern and need
``session`` instead. Logs carry ids, evaluator ids and outcomes only, never
response text.
"""

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from components.core.database import SessionScope
from components.core.schemas import Principal
from components.evaluation.enums import OutcomeStatus
from components.evaluation.evaluators import EVALUATORS, build_evaluator
from components.evaluation.judge import (
    JUDGE_EVALUATOR_VERSION,
    build_judge_prompt,
    parse_verdict,
)
from components.evaluation.repository import EvaluationRepository
from components.evaluation.schemas import (
    EvaluationInput,
    EvaluationOutcome,
    EvaluationResultSchema,
    EvaluationSummarySchema,
    EvaluatorConfigSchema,
    EvaluatorEntry,
    EvaluatorSummarySchema,
    LlmJudgeEntry,
    parse_evaluator_config,
)
from components.experiments.enums import ResultStatus
from components.experiments.exceptions import ExperimentNotFoundError
from components.experiments.repository import ExperimentRepository
from components.experiments.utils import backoff_delay
from components.third_party.llm.exceptions import LLMProviderError
from components.third_party.llm.schemas import ModelTargetSchema
from components.third_party.llm.service import LLMService

logger = logging.getLogger(__name__)

# Results enqueued per backfill request; bigger backlogs are processed in
# batches by calling again.
BACKFILL_BATCH_SIZE = 1000
# A judge call gets one retry on a retryable provider error; the project's
# experiment attempts/lease machinery would be disproportionate for a
# single evaluation call.
JUDGE_MAX_ATTEMPTS = 2
JUDGE_JITTER_MAX_SECONDS = 0.5

Sleep = Callable[[float], Awaitable[None]]


def _default_jitter() -> float:
    return random.uniform(  # nosec B311 - non-cryptographic retry jitter
        0, JUDGE_JITTER_MAX_SECONDS
    )


def _evaluator_version(entry: EvaluatorEntry) -> int:
    if isinstance(entry, LlmJudgeEntry):
        return JUDGE_EVALUATOR_VERSION
    return EVALUATORS[entry.type].version


class EvaluationService:
    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        scope: SessionScope | None = None,
        llm_service: LLMService | None = None,
        sleep: Sleep = asyncio.sleep,
        jitter: Callable[[], float] = _default_jitter,
    ) -> None:
        self._session = session
        self._scope = scope
        self._llm = llm_service
        self._sleep = sleep
        self._jitter = jitter

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError(
                "EvaluationService needs a session for this operation"
            )
        return self._session

    def _require_llm(self) -> LLMService:
        if self._llm is None:
            raise RuntimeError(
                "EvaluationService needs an llm_service to evaluate an "
                "llm_judge entry"
            )
        return self._llm

    def _require_scope(self) -> SessionScope:
        if self._scope is None:
            raise RuntimeError(
                "EvaluationService.evaluate_result needs a scope"
            )
        return self._scope

    async def _config(
        self,
        principal: Principal,
        experiment_id: uuid.UUID,
    ) -> EvaluatorConfigSchema:
        session = self._require_session()
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

    @staticmethod
    def _row(
        data: EvaluationInput, entry: EvaluatorEntry, outcome: EvaluationOutcome
    ) -> dict[str, Any]:
        return {
            "experiment_result_id": data.result_id,
            "experiment_id": data.experiment_id,
            "evaluator_id": entry.id,
            "evaluator_type": entry.type,
            "evaluator_version": _evaluator_version(entry),
            "status": outcome.status.value,
            "details": outcome.details,
        }

    @staticmethod
    def _evaluate_deterministic(
        entry: EvaluatorEntry, data: EvaluationInput
    ) -> EvaluationOutcome:
        try:
            if data.response_text is None:
                # Stored as error so the result is not pending forever.
                return EvaluationOutcome(
                    status=OutcomeStatus.ERROR,
                    details={"reason": "missing_response"},
                )
            return build_evaluator(entry).evaluate(
                data.response_text, data.expected_output
            )
        except Exception as exc:
            # Only the class name: messages may echo the response.
            return EvaluationOutcome(
                status=OutcomeStatus.ERROR,
                details={
                    "reason": "evaluator_exception",
                    "exception": type(exc).__name__,
                },
            )

    async def _evaluate_judge(
        self, entry: LlmJudgeEntry, data: EvaluationInput
    ) -> EvaluationOutcome:
        if data.response_text is None:
            return EvaluationOutcome(
                status=OutcomeStatus.ERROR,
                details={"reason": "missing_response"},
            )
        llm = self._require_llm()
        system_prompt, prompt = build_judge_prompt(
            entry.criteria,
            data.case_input,
            data.response_text,
            data.expected_output,
        )
        target = ModelTargetSchema(provider=entry.provider, model=entry.model)
        attempts = 0
        while True:
            attempts += 1
            try:
                response = await llm.generate(
                    target, prompt, system_prompt=system_prompt, temperature=0
                )
            except LLMProviderError as exc:
                if exc.retryable and attempts < JUDGE_MAX_ATTEMPTS:
                    await self._sleep(
                        backoff_delay(
                            0, exc.retry_after_seconds, self._jitter()
                        )
                    )
                    continue
                return EvaluationOutcome(
                    status=OutcomeStatus.ERROR,
                    details={
                        "reason": "judge_call_failed",
                        "error_kind": exc.kind.value,
                    },
                )
            except Exception as exc:
                return EvaluationOutcome(
                    status=OutcomeStatus.ERROR,
                    details={
                        "reason": "judge_call_exception",
                        "exception": type(exc).__name__,
                    },
                )
            break
        verdict = parse_verdict(response.text)
        if verdict is None:
            return EvaluationOutcome(
                status=OutcomeStatus.ERROR,
                details={"reason": "unparseable_verdict"},
            )
        return EvaluationOutcome(
            status=verdict, details={"reason": "judge_verdict"}
        )

    async def evaluate_result(self, result_id: uuid.UUID) -> int:
        """Store the outcome of every configured evaluator for one result.
        Idempotent: existing outcomes are left unchanged. Returns the number
        of rows inserted. Deterministic evaluators run in one short
        transaction (unchanged from before llm_judge existed); any
        llm_judge evaluators are called with no session open and stored in
        a second, separate transaction afterward."""
        scope = self._require_scope()
        async with scope() as session:
            data = await EvaluationRepository(session).get_input(result_id)
            if data is None:
                logger.info(
                    "result %s not found; nothing to evaluate", result_id
                )
                return 0
            if data.status != ResultStatus.SUCCEEDED.value:
                logger.info("result %s is %s; skipped", result_id, data.status)
                return 0
            config = self._parse(data.evaluator_config, data.experiment_id)
            deterministic = [
                e for e in config.evaluators if not isinstance(e, LlmJudgeEntry)
            ]
            judge_entries = [
                e for e in config.evaluators if isinstance(e, LlmJudgeEntry)
            ]
            if judge_entries and self._llm is None:
                # Fail before anything is persisted, including the
                # deterministic rows below: a misconfigured caller (an
                # llm_judge entry with no llm_service) must not leave this
                # result half-evaluated.
                raise RuntimeError(
                    "EvaluationService needs an llm_service to evaluate an "
                    "llm_judge entry"
                )
            rows = [
                self._row(
                    data, entry, self._evaluate_deterministic(entry, data)
                )
                for entry in deterministic
            ]
            inserted = await EvaluationRepository(session).insert_ignore(rows)
        logger.info(
            "result %s evaluated (%d deterministic, %d stored)",
            result_id,
            len(rows),
            inserted,
        )
        if not judge_entries:
            return inserted
        judge_rows = [
            self._row(data, entry, await self._evaluate_judge(entry, data))
            for entry in judge_entries
        ]
        async with scope() as session:
            judge_inserted = await EvaluationRepository(session).insert_ignore(
                judge_rows
            )
        logger.info(
            "result %s judge-evaluated (%d judge evaluators, %d stored)",
            result_id,
            len(judge_entries),
            judge_inserted,
        )
        return inserted + judge_inserted

    async def backfill(
        self,
        principal: Principal,
        experiment_id: uuid.UUID,
        limit: int | None = None,
    ) -> tuple[list[uuid.UUID], int]:
        """At most ``limit`` ids of succeeded results lacking some
        configured evaluation, for the caller to enqueue, and how many
        still lack one after this batch. Empty without evaluators."""
        session = self._require_session()
        limit = BACKFILL_BATCH_SIZE if limit is None else limit
        config = await self._config(principal, experiment_id)
        repository = EvaluationRepository(session)
        evaluator_ids = [e.id for e in config.evaluators]
        ids = await repository.list_unevaluated_result_ids(
            experiment_id, evaluator_ids, limit
        )
        total = await repository.count_unevaluated_results(
            experiment_id, evaluator_ids
        )
        # Read-only unit: end its transaction so the connection goes back to
        # the pool before the caller publishes to the broker. Not a commit;
        # the entry point's final commit on an idle session is a no-op.
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
        session = self._require_session()
        experiments = ExperimentRepository(session)
        if await experiments.get_scoped(experiment_id, principal) is None:
            raise ExperimentNotFoundError(experiment_id)
        rows, total = await EvaluationRepository(session).list_page(
            experiment_id, limit, offset, evaluator_id, status
        )
        items = [EvaluationResultSchema.model_validate(r) for r in rows]
        return items, total

    async def summary(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> EvaluationSummarySchema:
        session = self._require_session()
        config = await self._config(principal, experiment_id)
        repository = EvaluationRepository(session)
        counts = await repository.outcome_counts(experiment_id)
        unevaluated = await repository.count_unevaluated_results(
            experiment_id, [e.id for e in config.evaluators]
        )
        return EvaluationSummarySchema(
            evaluators=[
                EvaluatorSummarySchema(
                    id=entry.id,
                    type=entry.type,
                    version=_evaluator_version(entry),
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
