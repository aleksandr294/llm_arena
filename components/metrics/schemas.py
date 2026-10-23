"""Response schemas for experiment metrics.

Measured values (counts, latency, tokens) are kept apart from the estimated
cost. Unknown values are ``None``, never zero. Only ``CaseTargetResultSchema``
carries response text, and only when the caller asked for it.
"""

import uuid
from decimal import Decimal

from pydantic import BaseModel


class ResultCountsSchema(BaseModel):
    pending: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    total: int = 0


class SummarySchema(BaseModel):
    experiment_id: uuid.UUID
    experiment_status: str
    # True when no result is pending or running.
    finished: bool
    counts: ResultCountsSchema


class LatencyStatsSchema(BaseModel):
    """Milliseconds over succeeded results with a measured latency."""

    count: int = 0
    min_ms: float | None = None
    mean_ms: float | None = None
    median_ms: float | None = None
    p95_ms: float | None = None
    max_ms: float | None = None


class TokenSumSchema(BaseModel):
    """Sum over results that reported the field; ``None`` when none did."""

    total: int | None = None
    reported_count: int = 0


class TokenUsageSchema(BaseModel):
    # Denominator for each ``reported_count``.
    succeeded: int = 0
    input_tokens: TokenSumSchema
    output_tokens: TokenSumSchema
    total_tokens: TokenSumSchema


class EvaluatorMetricsSchema(BaseModel):
    id: str
    type: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    error: int = 0
    # passed / (passed + failed); None when neither exists.
    pass_rate: float | None = None


class RateInfoSchema(BaseModel):
    input_per_million: Decimal
    output_per_million: Decimal


class PricingInfoSchema(BaseModel):
    """Pricing used for the estimates; all null without a usable file."""

    version: str | None = None
    currency: str | None = None


class TargetCostSchema(BaseModel):
    """An estimate, not a measurement."""

    priced: bool
    estimated_cost: Decimal | None = None
    currency: str | None = None
    pricing_version: str | None = None
    rates: RateInfoSchema | None = None
    results_included: int = 0
    # Succeeded results lacking input or output tokens.
    results_excluded: int = 0


class TargetComparisonSchema(BaseModel):
    provider: str
    model: str
    counts: ResultCountsSchema
    # failed / (succeeded + failed); None when neither exists.
    error_rate: float | None = None
    error_kinds: dict[str, int]
    latency: LatencyStatsSchema
    tokens: TokenUsageSchema
    evaluators: list[EvaluatorMetricsSchema]
    cost: TargetCostSchema


class ComparisonSchema(BaseModel):
    experiment_id: uuid.UUID
    experiment_status: str
    finished: bool
    pricing: PricingInfoSchema
    # The reason (never the file path or contents) when the pricing file is
    # unusable.
    pricing_error: str | None = None
    targets: list[TargetComparisonSchema]


class CaseEvaluationSchema(BaseModel):
    evaluator_id: str
    status: str


class CaseTargetResultSchema(BaseModel):
    provider: str
    model: str
    status: str
    attempts: int
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    error_kind: str | None = None
    evaluations: list[CaseEvaluationSchema]
    # Only filled when responses are requested.
    response_text: str | None = None


class CaseMetricsSchema(BaseModel):
    case_position: int
    test_case_id: uuid.UUID
    results: list[CaseTargetResultSchema]


class CasePageSchema(BaseModel):
    items: list[CaseMetricsSchema]
    total: int
    limit: int
    offset: int
