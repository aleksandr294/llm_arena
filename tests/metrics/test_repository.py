"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.metrics.repository import MetricsRepository
from tests.metrics.helpers import Row, seed

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


async def test_status_counts_exclude_pending_and_cancelled_from_stats(
    session_factory: Factory,
) -> None:
    rows = [
        Row(1, latency=100, tokens=(10, 5, 15)),
        Row(2, latency=200, tokens=(20, 5, 25)),
        Row(3, latency=300, tokens=(None, None, None)),
        Row(4, status="failed", error_kind="timeout", latency=999),
        Row(5, status="cancelled", latency=999, tokens=(100, 100, 200)),
        Row(6, status="pending"),
        Row(7, status="running"),
    ]
    experiment_id = await seed(session_factory, rows)
    async with session_factory() as session:
        aggs = await MetricsRepository(session).target_aggregates(experiment_id)
    agg = aggs[("openai", "m1")]
    assert (agg.pending, agg.running, agg.succeeded) == (1, 1, 3)
    assert (agg.failed, agg.cancelled) == (1, 1)
    # Only succeeded results feed latency and tokens.
    assert agg.latency_count == 3
    assert agg.latency_min == 100
    assert agg.latency_max == 300
    assert agg.latency_mean == 200
    assert agg.latency_median == 200
    assert agg.latency_p95 == pytest.approx(290)  # 200 + 0.9 * 100
    assert (agg.input_sum, agg.input_count) == (30, 2)
    assert (agg.output_sum, agg.output_count) == (10, 2)
    assert (agg.total_sum, agg.total_count) == (40, 2)
    assert agg.paired_count == 2
    assert (agg.paired_input_sum, agg.paired_output_sum) == (30, 10)


async def test_all_unknown_values_are_null_not_zero(
    session_factory: Factory,
) -> None:
    experiment_id = await seed(session_factory, [Row(1), Row(2)])
    async with session_factory() as session:
        aggs = await MetricsRepository(session).target_aggregates(experiment_id)
    agg = aggs[("openai", "m1")]
    assert agg.succeeded == 2
    assert agg.latency_count == 0
    assert agg.latency_min is None and agg.latency_mean is None
    assert agg.latency_median is None and agg.latency_p95 is None
    assert agg.latency_max is None
    assert agg.input_sum is None and agg.input_count == 0
    assert agg.total_sum is None
    assert agg.paired_count == 0 and agg.paired_input_sum is None


async def test_partial_tokens_are_not_paired(session_factory: Factory) -> None:
    rows = [
        Row(1, tokens=(10, None, None)),
        Row(2, tokens=(20, 7, None)),
        Row(3, tokens=(None, 9, 9)),
    ]
    experiment_id = await seed(session_factory, rows)
    async with session_factory() as session:
        agg = (
            await MetricsRepository(session).target_aggregates(experiment_id)
        )[("openai", "m1")]
    assert (agg.input_sum, agg.input_count) == (30, 2)
    assert (agg.output_sum, agg.output_count) == (16, 2)
    assert (agg.total_sum, agg.total_count) == (9, 1)
    assert agg.paired_count == 1
    assert (agg.paired_input_sum, agg.paired_output_sum) == (20, 7)


async def test_targets_are_grouped_and_other_experiments_ignored(
    session_factory: Factory,
) -> None:
    rows = [
        Row(1, model="m1", latency=10),
        Row(1, model="m2", latency=50),
        Row(2, model="m2", status="failed", error_kind="timeout"),
        Row(3, model="m2", status="failed", error_kind="timeout"),
        Row(4, model="m2", status="failed"),
        Row(5, model="m2", status="failed", error_kind="rate_limit"),
    ]
    experiment_id = await seed(session_factory, rows, models=("m1", "m2"))
    await seed(session_factory, [Row(1, model="m1", latency=1)])
    async with session_factory() as session:
        repo = MetricsRepository(session)
        aggs = await repo.target_aggregates(experiment_id)
        kinds = await repo.error_kinds(experiment_id)
    assert aggs[("openai", "m1")].succeeded == 1
    assert aggs[("openai", "m1")].latency_max == 10
    assert aggs[("openai", "m2")].failed == 4
    assert kinds == {
        ("openai", "m2"): {"timeout": 2, "unknown": 1, "rate_limit": 1}
    }


async def test_target_without_results_is_absent(
    session_factory: Factory,
) -> None:
    experiment_id = await seed(
        session_factory, [Row(1, model="m1")], models=("m1", "m2")
    )
    async with session_factory() as session:
        aggs = await MetricsRepository(session).target_aggregates(experiment_id)
    assert set(aggs) == {("openai", "m1")}
    async with session_factory() as session:
        empty = await MetricsRepository(session).target_aggregates(uuid.uuid4())
    assert empty == {}


async def test_evaluator_outcomes_per_target(session_factory: Factory) -> None:
    rows = [
        Row(1, evals={"a": "passed", "b": "failed"}),
        Row(2, evals={"a": "passed"}),
        Row(3, evals={"a": "failed"}),
        Row(4, evals={"a": "skipped"}),
        Row(1, model="m2", evals={"a": "error"}),
    ]
    experiment_id = await seed(
        session_factory, rows, models=("m1", "m2"), evaluators=("a", "b")
    )
    async with session_factory() as session:
        outcomes = await MetricsRepository(session).evaluator_outcomes(
            experiment_id
        )
    assert outcomes == {
        ("openai", "m1", "a"): {"passed": 2, "failed": 1, "skipped": 1},
        ("openai", "m1", "b"): {"failed": 1},
        ("openai", "m2", "a"): {"error": 1},
    }


async def test_case_page_and_results(session_factory: Factory) -> None:
    rows = [
        Row(p, model=m, text=f"r{p}{m}", evals={"a": "passed"})
        for p in (1, 2, 3)
        for m in ("m1", "m2")
    ]
    experiment_id = await seed(
        session_factory, rows, models=("m1", "m2"), evaluators=("a",)
    )
    async with session_factory() as session:
        repo = MetricsRepository(session)
        page, total = await repo.case_page(experiment_id, 2, 1)
        assert total == 3
        assert [c.case_position for c in page] == [2, 3]
        results = await repo.case_results(experiment_id, [2, 3], False)
        assert [(r.case_position, r.model) for r in results] == [
            (2, "m1"),
            (2, "m2"),
            (3, "m1"),
            (3, "m2"),
        ]
        assert all(r.response_text is None for r in results)
        with_text = await repo.case_results(experiment_id, [2], True)
        assert [r.response_text for r in with_text] == ["r2m1", "r2m2"]
        evals = await repo.case_evaluations(experiment_id, [2, 3])
        assert len(evals) == 4
        assert {e.result_id for e in evals} == {r.result_id for r in results}
        empty, total_empty = await repo.case_page(uuid.uuid4(), 10, 0)
        assert empty == [] and total_empty == 0
