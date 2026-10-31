"""Model-call metrics: latency, an approximate generation speed, and
estimated cost. Labels are provider and model (and currency for cost) only
— never a prompt, response, request id or anything else of unbounded
cardinality. Generation speed is derived from total call latency, not a
first-token timestamp (the provider adapters are not streaming); it is
named and documented as an approximation, never presented as TTFT.
"""

from decimal import Decimal

from prometheus_client import Counter, Histogram

from components.core.worker_metrics import WORKER_METRICS_REGISTRY
from components.experiments.schemas import MAX_TIMEOUT_SECONDS

# prometheus_client's default buckets top out at 10s; a request's timeout
# can be configured up to MAX_TIMEOUT_SECONDS, well past that, which would
# push most calls into the +Inf bucket and make histogram_quantile() on
# this metric meaningless (see the starter dashboard's p95 panels).
_LATENCY_BUCKETS = (
    0.5,
    1,
    2.5,
    5,
    10,
    20,
    30,
    60,
    MAX_TIMEOUT_SECONDS,
    float("inf"),
)

MODEL_CALL_LATENCY_SECONDS = Histogram(
    "model_call_latency_seconds",
    "LLM provider call latency by provider and model.",
    ("provider", "model"),
    buckets=_LATENCY_BUCKETS,
    registry=WORKER_METRICS_REGISTRY,
)
MODEL_CALL_GENERATION_SPEED_TOKENS_PER_SECOND = Histogram(
    "model_call_generation_speed_tokens_per_second",
    "Approximate generation speed (output tokens / total call latency) by "
    "provider and model. Not a time-to-first-token measurement: the "
    "provider adapters are not streaming.",
    ("provider", "model"),
    registry=WORKER_METRICS_REGISTRY,
)
MODEL_CALL_COST_ESTIMATE_TOTAL = Counter(
    "model_call_cost_estimate_total",
    "Estimated cumulative cost of model calls by provider, model and "
    "currency. Absent entirely when no pricing is configured for that "
    "provider and model, never recorded as zero.",
    ("provider", "model", "currency"),
    registry=WORKER_METRICS_REGISTRY,
)


def record_latency(provider: str, model: str, latency_ms: float) -> None:
    MODEL_CALL_LATENCY_SECONDS.labels(provider=provider, model=model).observe(
        latency_ms / 1000
    )


def record_generation_speed(
    provider: str,
    model: str,
    output_tokens: int | None,
    latency_ms: float,
) -> None:
    if output_tokens is None or latency_ms <= 0:
        return
    tokens_per_second = output_tokens / (latency_ms / 1000)
    MODEL_CALL_GENERATION_SPEED_TOKENS_PER_SECOND.labels(
        provider=provider, model=model
    ).observe(tokens_per_second)


def record_cost(
    provider: str, model: str, currency: str, cost: Decimal
) -> None:
    MODEL_CALL_COST_ESTIMATE_TOTAL.labels(
        provider=provider, model=model, currency=currency
    ).inc(float(cost))
