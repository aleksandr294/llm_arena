"""Task latency and outcome metrics for the Taskiq worker.

Taskiq's own receiver already catches every exception raised inside a task
and turns it into a ``TaskiqResult(is_err=True, ...)`` before calling
``post_execute`` — the exception itself never reaches a middleware. One
hook is therefore enough to see every task's latency and outcome.
"""

import logging
from typing import Any

from prometheus_client import Counter, Histogram
from taskiq import TaskiqMessage, TaskiqMiddleware
from taskiq.result import TaskiqResult

from components.core.worker_metrics import WORKER_METRICS_REGISTRY

logger = logging.getLogger(__name__)

TASK_RUNS_TOTAL = Counter(
    "task_runs_total",
    "Background task runs by task name and outcome.",
    ("task_name", "outcome"),
    registry=WORKER_METRICS_REGISTRY,
)
# A task wraps a whole provider-call retry loop (up to MAX_ATTEMPTS calls
# at up to MAX_TIMEOUT_SECONDS each, plus backoff sleeps up to
# BACKOFF_CAP_SECONDS between them), so the default buckets (<=10s) would
# put most real task runs in +Inf and make histogram_quantile() on this
# metric meaningless.
_TASK_DURATION_BUCKETS = (1, 5, 10, 30, 60, 120, 300, 600, 900, float("inf"))

TASK_DURATION_SECONDS = Histogram(
    "task_duration_seconds",
    "Background task execution time by task name.",
    ("task_name",),
    buckets=_TASK_DURATION_BUCKETS,
    registry=WORKER_METRICS_REGISTRY,
)


class TaskMetricsMiddleware(TaskiqMiddleware):
    """Records one observation per finished task run. Errors in this hook
    must never escape: that would break the receiver loop around every
    real task's result, so any unexpected failure here is swallowed after
    logging, never raised."""

    async def post_execute(
        self, message: TaskiqMessage, result: TaskiqResult[Any]
    ) -> None:
        try:
            outcome = "failed" if result.is_err else "succeeded"
            TASK_RUNS_TOTAL.labels(
                task_name=message.task_name, outcome=outcome
            ).inc()
            TASK_DURATION_SECONDS.labels(task_name=message.task_name).observe(
                result.execution_time
            )
        except Exception:
            logger.warning("task metrics recording failed")
