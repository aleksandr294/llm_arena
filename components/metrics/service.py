"""Experiment metrics composed from stored results.

Read-only: nothing is persisted, costs are computed at request time from the
operator's pricing file. Logs, if any, must carry ids and counts only.
"""

import logging
import uuid
from collections import defaultdict

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from components.core.exceptions import PricingConfigError
from components.core.pricing import (
    PricingFileSchema,
    estimate_cost,
    find_rate,
    load_pricing,
)
from components.core.schemas import Principal
from components.evaluation.schemas import (
    EvaluatorConfigSchema,
    parse_evaluator_config,
)
from components.experiments.enums import ResultStatus
from components.experiments.exceptions import ExperimentNotFoundError
from components.experiments.models import Experiment
from components.experiments.repository import (
    ExperimentRepository,
    ExperimentResultRepository,
)
from components.experiments.schemas import ExperimentConfigSchema
from components.metrics.repository import (
    MetricsRepository,
    TargetAggregate,
)
from components.metrics.schemas import (
    CaseEvaluationSchema,
    CaseMetricsSchema,
    CasePageSchema,
    CaseTargetResultSchema,
    ComparisonSchema,
    EvaluatorMetricsSchema,
    LatencyStatsSchema,
    PricingInfoSchema,
    RateInfoSchema,
    ResultCountsSchema,
    SummarySchema,
    TargetComparisonSchema,
    TargetCostSchema,
    TokenSumSchema,
    TokenUsageSchema,
)

logger = logging.getLogger(__name__)


def _counts_from(statuses: dict[str, int]) -> ResultCountsSchema:
    return ResultCountsSchema(
        pending=statuses.get(ResultStatus.PENDING.value, 0),
        running=statuses.get(ResultStatus.RUNNING.value, 0),
        succeeded=statuses.get(ResultStatus.SUCCEEDED.value, 0),
        failed=statuses.get(ResultStatus.FAILED.value, 0),
        cancelled=statuses.get(ResultStatus.CANCELLED.value, 0),
        total=sum(statuses.values()),
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


class MetricsService:
    """Uses the caller's session and never commits.

    ``pricing_file`` is the optional ``PRICING_FILE`` path; ``None`` means no
    cost estimates.
    """

    def __init__(
        self, session: AsyncSession, pricing_file: str | None = None
    ) -> None:
        self._session = session
        self._pricing_file = pricing_file
        self._metrics = MetricsRepository(session)

    async def _experiment(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> Experiment:
        # Metrics inherit the access of their experiment; anything outside
        # the caller's scope is reported as not found.
        experiment = await ExperimentRepository(self._session).get_scoped(
            experiment_id, principal
        )
        if experiment is None:
            raise ExperimentNotFoundError(experiment_id)
        return experiment

    async def summary(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> SummarySchema:
        experiment = await self._experiment(principal, experiment_id)
        statuses = await ExperimentResultRepository(
            self._session
        ).status_counts(experiment_id)
        counts = _counts_from(statuses)
        return SummarySchema(
            experiment_id=experiment_id,
            experiment_status=experiment.status,
            finished=counts.pending + counts.running == 0,
            counts=counts,
        )

    async def comparison(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> ComparisonSchema:
        experiment = await self._experiment(principal, experiment_id)
        pricing, pricing_error = self._load_pricing()
        aggregates = await self._metrics.target_aggregates(experiment_id)
        kinds = await self._metrics.error_kinds(experiment_id)
        outcomes = await self._metrics.evaluator_outcomes(experiment_id)
        evaluators = self._evaluator_config(experiment).evaluators
        # Targets come from the frozen config so a target without results
        # still appears.
        targets = ExperimentConfigSchema.model_validate(
            experiment.config
        ).targets

        items: list[TargetComparisonSchema] = []
        total_open = 0
        for target in targets:
            provider = target.provider.value
            key = (provider, target.model)
            agg = aggregates.get(key)
            counts = ResultCountsSchema()
            if agg is not None:
                counts = _counts_from(
                    {
                        "pending": agg.pending,
                        "running": agg.running,
                        "succeeded": agg.succeeded,
                        "failed": agg.failed,
                        "cancelled": agg.cancelled,
                    }
                )
            total_open += counts.pending + counts.running
            items.append(
                TargetComparisonSchema(
                    provider=provider,
                    model=target.model,
                    counts=counts,
                    error_rate=_rate(
                        counts.failed, counts.succeeded + counts.failed
                    ),
                    error_kinds=kinds.get(key, {}),
                    latency=self._latency(agg),
                    tokens=self._tokens(agg, counts.succeeded),
                    evaluators=[
                        self._evaluator(
                            e.id, e.type, outcomes.get((*key, e.id), {})
                        )
                        for e in evaluators
                    ],
                    cost=self._cost(pricing, provider, target.model, agg),
                )
            )
        return ComparisonSchema(
            experiment_id=experiment_id,
            experiment_status=experiment.status,
            finished=total_open == 0,
            pricing=PricingInfoSchema(
                version=pricing.version if pricing else None,
                currency=pricing.currency if pricing else None,
            ),
            pricing_error=pricing_error,
            targets=items,
        )

    async def cases(
        self,
        principal: Principal,
        experiment_id: uuid.UUID,
        limit: int,
        offset: int,
        include_responses: bool = False,
    ) -> CasePageSchema:
        await self._experiment(principal, experiment_id)
        refs, total = await self._metrics.case_page(
            experiment_id, limit, offset
        )
        if not refs:
            return CasePageSchema(
                items=[], total=total, limit=limit, offset=offset
            )
        positions = [r.case_position for r in refs]
        rows = await self._metrics.case_results(
            experiment_id, positions, include_responses
        )
        evaluations = defaultdict(list)
        for ev in await self._metrics.case_evaluations(
            experiment_id, positions
        ):
            evaluations[ev.result_id].append(
                CaseEvaluationSchema(
                    evaluator_id=ev.evaluator_id, status=ev.status
                )
            )
        by_position: dict[int, list[CaseTargetResultSchema]] = defaultdict(list)
        for row in rows:
            by_position[row.case_position].append(
                CaseTargetResultSchema(
                    provider=row.provider,
                    model=row.model,
                    status=row.status,
                    attempts=row.attempts,
                    latency_ms=row.latency_ms,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    total_tokens=row.total_tokens,
                    error_kind=row.error_kind,
                    evaluations=evaluations.get(row.result_id, []),
                    response_text=row.response_text,
                )
            )
        return CasePageSchema(
            items=[
                CaseMetricsSchema(
                    case_position=r.case_position,
                    test_case_id=r.test_case_id,
                    results=by_position.get(r.case_position, []),
                )
                for r in refs
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    def _load_pricing(self) -> tuple[PricingFileSchema | None, str | None]:
        """Pricing problems degrade cost fields only, not the response."""
        try:
            return load_pricing(self._pricing_file), None
        except PricingConfigError as exc:
            # The path is operator configuration: it goes to the server log,
            # never to the API caller.
            logger.warning(
                "Pricing file %r is unusable: %s", exc.path, exc.reason
            )
            return None, f"Pricing file is unusable: {exc.reason}"

    @staticmethod
    def _evaluator_config(experiment: Experiment) -> EvaluatorConfigSchema:
        try:
            return parse_evaluator_config(experiment.evaluator_config)
        except ValidationError:
            # Legacy configs that no longer parse mean no evaluators, as in
            # the evaluation service.
            return EvaluatorConfigSchema()

    @staticmethod
    def _latency(agg: TargetAggregate | None) -> LatencyStatsSchema:
        if agg is None:
            return LatencyStatsSchema()
        return LatencyStatsSchema(
            count=agg.latency_count,
            min_ms=agg.latency_min,
            mean_ms=agg.latency_mean,
            median_ms=agg.latency_median,
            p95_ms=agg.latency_p95,
            max_ms=agg.latency_max,
        )

    @staticmethod
    def _tokens(
        agg: TargetAggregate | None, succeeded: int
    ) -> TokenUsageSchema:
        if agg is None:
            return TokenUsageSchema(
                input_tokens=TokenSumSchema(),
                output_tokens=TokenSumSchema(),
                total_tokens=TokenSumSchema(),
            )
        return TokenUsageSchema(
            succeeded=succeeded,
            input_tokens=TokenSumSchema(
                total=agg.input_sum, reported_count=agg.input_count
            ),
            output_tokens=TokenSumSchema(
                total=agg.output_sum, reported_count=agg.output_count
            ),
            total_tokens=TokenSumSchema(
                total=agg.total_sum, reported_count=agg.total_count
            ),
        )

    @staticmethod
    def _evaluator(
        evaluator_id: str, evaluator_type: str, counts: dict[str, int]
    ) -> EvaluatorMetricsSchema:
        passed = counts.get("passed", 0)
        failed = counts.get("failed", 0)
        return EvaluatorMetricsSchema(
            id=evaluator_id,
            type=evaluator_type,
            passed=passed,
            failed=failed,
            skipped=counts.get("skipped", 0),
            error=counts.get("error", 0),
            pass_rate=_rate(passed, passed + failed),
        )

    @staticmethod
    def _cost(
        pricing: PricingFileSchema | None,
        provider: str,
        model: str,
        agg: TargetAggregate | None,
    ) -> TargetCostSchema:
        if pricing is None:
            return TargetCostSchema(priced=False)
        rate = find_rate(pricing, provider, model)
        if rate is None:
            return TargetCostSchema(
                priced=False,
                currency=pricing.currency,
                pricing_version=pricing.version,
            )
        included = agg.paired_count if agg else 0
        excluded = (agg.succeeded if agg else 0) - included
        cost = None
        if agg is not None and included > 0:
            cost = estimate_cost(
                rate, agg.paired_input_sum, agg.paired_output_sum
            )
        return TargetCostSchema(
            priced=True,
            estimated_cost=cost,
            currency=pricing.currency,
            pricing_version=pricing.version,
            rates=RateInfoSchema(
                input_per_million=rate.input_per_million,
                output_per_million=rate.output_per_million,
            ),
            results_included=included,
            results_excluded=excluded,
        )
