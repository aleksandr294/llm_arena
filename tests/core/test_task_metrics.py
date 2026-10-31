"""Task latency and failure metrics, via a real broker so Taskiq's own
exception handling (which swallows the exception before any middleware
hook runs) is exercised rather than assumed."""

from unittest.mock import patch

from taskiq.brokers.inmemory_broker import InMemoryBroker
from taskiq.message import TaskiqMessage
from taskiq.result import TaskiqResult

from components.core.task_metrics import (
    TASK_DURATION_SECONDS,
    TASK_RUNS_TOTAL,
    TaskMetricsMiddleware,
)


def _counter(task_name: str, outcome: str) -> float:
    value: float = TASK_RUNS_TOTAL.labels(
        task_name=task_name, outcome=outcome
    )._value.get()
    return value


def _make_broker() -> InMemoryBroker:
    broker = InMemoryBroker(await_inplace=True)
    broker.add_middlewares(TaskMetricsMiddleware())
    return broker


async def test_successful_task_is_counted() -> None:
    broker = _make_broker()

    @broker.task(task_name="metrics_test.ok")
    async def ok_task() -> str:
        return "done"

    before = _counter("metrics_test.ok", "succeeded")
    kicked = await ok_task.kiq()
    result = await kicked.wait_result()
    assert result.is_err is False
    assert _counter("metrics_test.ok", "succeeded") == before + 1


async def test_failing_task_is_counted_and_result_marks_error() -> None:
    broker = _make_broker()

    @broker.task(task_name="metrics_test.fail")
    async def failing_task() -> None:
        raise RuntimeError("boom")

    before = _counter("metrics_test.fail", "failed")
    kicked = await failing_task.kiq()
    result = await kicked.wait_result()
    # Taskiq catches the exception itself; it never propagates here.
    assert result.is_err is True
    assert _counter("metrics_test.fail", "failed") == before + 1


async def test_latency_is_recorded() -> None:
    broker = _make_broker()

    @broker.task(task_name="metrics_test.timed")
    async def timed_task() -> str:
        return "done"

    # Histogram children expose no cumulative-count accessor; summing every
    # per-bucket value (each observation lands in exactly one) gives it.
    def _observation_count() -> float:
        child = TASK_DURATION_SECONDS.labels(task_name="metrics_test.timed")
        return float(sum(bucket.get() for bucket in child._buckets))

    before = _observation_count()
    kicked = await timed_task.kiq()
    await kicked.wait_result()
    assert _observation_count() == before + 1


async def test_post_execute_swallows_its_own_failure() -> None:
    """A problem recording the metric (unexpected label, registry error,
    ...) must never propagate out of post_execute; that would break the
    receiver loop around every real task's result."""
    middleware = TaskMetricsMiddleware()
    message = TaskiqMessage(
        task_id="x",
        task_name="metrics_test.broken",
        labels={},
        args=[],
        kwargs={},
    )
    result: TaskiqResult[None] = TaskiqResult(
        is_err=False, return_value=None, execution_time=0.01
    )
    with patch(
        "components.core.task_metrics.TASK_RUNS_TOTAL.labels",
        side_effect=RuntimeError("boom"),
    ):
        await middleware.post_execute(message, result)  # must not raise
