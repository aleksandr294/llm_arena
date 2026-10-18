"""Read-only aggregates over experiment and evaluation results.

Everything is computed in SQL, grouped by ``(provider, model)``, from the
rows committed at query time. Nothing here returns prompts; response text
is selected only when explicitly requested.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Text, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import FunctionFilter

from components.evaluation.models import EvaluationResult
from components.experiments.enums import ResultStatus
from components.experiments.models import ExperimentResult

TargetKey = tuple[str, str]

_R = ExperimentResult
_SUCCEEDED = ResultStatus.SUCCEEDED.value
UNKNOWN_ERROR_KIND = "unknown"


@dataclass(frozen=True)
class TargetAggregate:
    pending: int
    running: int
    succeeded: int
    failed: int
    cancelled: int
    latency_count: int
    latency_min: float | None
    latency_mean: float | None
    latency_median: float | None
    latency_p95: float | None
    latency_max: float | None
    input_sum: int | None
    input_count: int
    output_sum: int | None
    output_count: int
    total_sum: int | None
    total_count: int
    # Succeeded results with both input and output tokens known, and the
    # sums over exactly those, which is the cost basis.
    paired_count: int
    paired_input_sum: int | None
    paired_output_sum: int | None


@dataclass(frozen=True)
class CaseRef:
    case_position: int
    test_case_id: uuid.UUID


@dataclass(frozen=True)
class CaseResultRow:
    result_id: uuid.UUID
    case_position: int
    provider: str
    model: str
    status: str
    attempts: int
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    error_kind: str | None
    response_text: str | None


@dataclass(frozen=True)
class CaseEvaluationRow:
    result_id: uuid.UUID
    evaluator_id: str
    status: str


def _count(status: ResultStatus) -> FunctionFilter[int]:
    return func.count().filter(_R.status == status.value)


def _int(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[call-overload]


class MetricsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def target_aggregates(
        self, experiment_id: uuid.UUID
    ) -> dict[TargetKey, TargetAggregate]:
        """Status counts, latency and token aggregates per target. Targets
        without results are absent."""
        ok = _R.status == _SUCCEEDED
        timed = ok & _R.latency_ms.is_not(None)
        paired = (
            ok & _R.input_tokens.is_not(None) & _R.output_tokens.is_not(None)
        )
        stmt = (
            select(
                _R.provider,
                _R.model,
                _count(ResultStatus.PENDING),
                _count(ResultStatus.RUNNING),
                _count(ResultStatus.SUCCEEDED),
                _count(ResultStatus.FAILED),
                _count(ResultStatus.CANCELLED),
                func.count(_R.latency_ms).filter(ok),
                func.min(_R.latency_ms).filter(timed),
                func.avg(_R.latency_ms).filter(timed),
                func.percentile_cont(0.5)
                .within_group(_R.latency_ms)
                .filter(timed),
                func.percentile_cont(0.95)
                .within_group(_R.latency_ms)
                .filter(timed),
                func.max(_R.latency_ms).filter(timed),
                func.sum(_R.input_tokens).filter(ok),
                func.count(_R.input_tokens).filter(ok),
                func.sum(_R.output_tokens).filter(ok),
                func.count(_R.output_tokens).filter(ok),
                func.sum(_R.total_tokens).filter(ok),
                func.count(_R.total_tokens).filter(ok),
                func.count().filter(paired),
                func.sum(_R.input_tokens).filter(paired),
                func.sum(_R.output_tokens).filter(paired),
            )
            .where(_R.experiment_id == experiment_id)
            .group_by(_R.provider, _R.model)
        )
        rows = (await self.session.execute(stmt)).all()
        return {
            (r[0], r[1]): TargetAggregate(
                pending=r[2],
                running=r[3],
                succeeded=r[4],
                failed=r[5],
                cancelled=r[6],
                latency_count=r[7],
                latency_min=r[8],
                latency_mean=r[9],
                latency_median=r[10],
                latency_p95=r[11],
                latency_max=r[12],
                input_sum=_int(r[13]),
                input_count=r[14],
                output_sum=_int(r[15]),
                output_count=r[16],
                total_sum=_int(r[17]),
                total_count=r[18],
                paired_count=r[19],
                paired_input_sum=_int(r[20]),
                paired_output_sum=_int(r[21]),
            )
            for r in rows
        }

    async def error_kinds(
        self, experiment_id: uuid.UUID
    ) -> dict[TargetKey, dict[str, int]]:
        """Failed results per target and error kind. A failure without a
        stored kind is counted under ``unknown``."""
        kind = func.coalesce(_R.error_kind, UNKNOWN_ERROR_KIND)
        rows = await self.session.execute(
            select(_R.provider, _R.model, kind, func.count())
            .where(
                _R.experiment_id == experiment_id,
                _R.status == ResultStatus.FAILED.value,
            )
            .group_by(_R.provider, _R.model, kind)
        )
        result: dict[TargetKey, dict[str, int]] = {}
        for provider, model, error_kind, count in rows.all():
            result.setdefault((provider, model), {})[error_kind] = count
        return result

    async def evaluator_outcomes(
        self, experiment_id: uuid.UUID
    ) -> dict[tuple[str, str, str], dict[str, int]]:
        """Outcome counts keyed by ``(provider, model, evaluator_id)``."""
        rows = await self.session.execute(
            select(
                _R.provider,
                _R.model,
                EvaluationResult.evaluator_id,
                EvaluationResult.status,
                func.count(),
            )
            .join(_R, _R.id == EvaluationResult.experiment_result_id)
            .where(EvaluationResult.experiment_id == experiment_id)
            .group_by(
                _R.provider,
                _R.model,
                EvaluationResult.evaluator_id,
                EvaluationResult.status,
            )
        )
        result: dict[tuple[str, str, str], dict[str, int]] = {}
        for provider, model, evaluator_id, status, count in rows.all():
            result.setdefault((provider, model, evaluator_id), {})[
                status
            ] = count
        return result

    async def case_page(
        self, experiment_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[list[CaseRef], int]:
        """One page of distinct cases in position order, and the total."""
        total = await self.session.scalar(
            select(func.count(func.distinct(_R.case_position))).where(
                _R.experiment_id == experiment_id
            )
        )
        rows = await self.session.execute(
            select(_R.case_position, _R.test_case_id)
            .where(_R.experiment_id == experiment_id)
            .group_by(_R.case_position, _R.test_case_id)
            .order_by(_R.case_position)
            .limit(limit)
            .offset(offset)
        )
        return [CaseRef(p, t) for p, t in rows.all()], total or 0

    async def case_results(
        self,
        experiment_id: uuid.UUID,
        positions: Sequence[int],
        include_responses: bool,
    ) -> list[CaseResultRow]:
        """Results of the given case positions ordered by position then
        target. Response text is read only when ``include_responses``."""
        response = (
            _R.response_text
            if include_responses
            else literal(None, type_=Text).label("response_text")
        )
        rows = await self.session.execute(
            select(
                _R.id,
                _R.case_position,
                _R.provider,
                _R.model,
                _R.status,
                _R.attempts,
                _R.latency_ms,
                _R.input_tokens,
                _R.output_tokens,
                _R.total_tokens,
                _R.error_kind,
                response,
            )
            .where(
                _R.experiment_id == experiment_id,
                _R.case_position.in_(positions),
            )
            .order_by(_R.case_position, _R.provider, _R.model, _R.id)
        )
        return [
            CaseResultRow(
                result_id=r[0],
                case_position=r[1],
                provider=r[2],
                model=r[3],
                status=r[4],
                attempts=r[5],
                latency_ms=r[6],
                input_tokens=r[7],
                output_tokens=r[8],
                total_tokens=r[9],
                error_kind=r[10],
                response_text=r[11],
            )
            for r in rows.all()
        ]

    async def case_evaluations(
        self, experiment_id: uuid.UUID, positions: Sequence[int]
    ) -> list[CaseEvaluationRow]:
        rows = await self.session.execute(
            select(
                EvaluationResult.experiment_result_id,
                EvaluationResult.evaluator_id,
                EvaluationResult.status,
            )
            .join(_R, _R.id == EvaluationResult.experiment_result_id)
            .where(
                _R.experiment_id == experiment_id,
                _R.case_position.in_(positions),
            )
            .order_by(EvaluationResult.evaluator_id)
        )
        return [CaseEvaluationRow(*row) for row in rows.all()]
