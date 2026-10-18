"""Service tests over seeded data: need PostgreSQL (``DATABASE_URL``)."""

import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.experiments.exceptions import ExperimentNotFoundError
from components.metrics.schemas import ComparisonSchema
from components.metrics.service import MetricsService
from tests.access import ROOT
from tests.metrics.helpers import Row, seed

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


def _pricing(
    tmp_path: Path, input_rate: object = 1.00, output_rate: object = 2.00
) -> str:
    path = tmp_path / "pricing.json"
    path.write_text(
        json.dumps(
            {
                "version": "p-1",
                "currency": "USD",
                "rates": {
                    "openai": {
                        "m1": {
                            "input_per_million": input_rate,
                            "output_per_million": output_rate,
                        }
                    }
                },
            }
        )
    )
    return str(path)


async def _comparison(
    factory: Factory, experiment_id: uuid.UUID, pricing: str | None = None
) -> ComparisonSchema:
    async with factory() as session:
        return await MetricsService(session, pricing).comparison(
            ROOT, experiment_id
        )


async def test_summary_mixed_statuses(session_factory: Factory) -> None:
    rows = [Row(1), Row(2), Row(3), Row(4, status="failed")] + [
        Row(5, status="pending"),
        Row(6, status="pending"),
    ]
    experiment_id = await seed(session_factory, rows)
    async with session_factory() as session:
        summary = await MetricsService(session).summary(ROOT, experiment_id)
    assert summary.counts.succeeded == 3
    assert summary.counts.failed == 1
    assert summary.counts.pending == 2
    assert summary.counts.total == 6
    assert summary.finished is False
    assert summary.experiment_status == "completed"


async def test_summary_finished(session_factory: Factory) -> None:
    experiment_id = await seed(
        session_factory, [Row(1), Row(2, status="failed")]
    )
    async with session_factory() as session:
        summary = await MetricsService(session).summary(ROOT, experiment_id)
    assert summary.finished is True


async def test_error_rate_excludes_cancelled(session_factory: Factory) -> None:
    rows = [
        Row(1),
        Row(2),
        Row(3),
        Row(4, status="failed", error_kind="timeout"),
        Row(5, status="cancelled"),
    ]
    experiment_id = await seed(session_factory, rows)
    comp = await _comparison(session_factory, experiment_id)
    target = comp.targets[0]
    assert target.error_rate == 0.25
    assert target.counts.cancelled == 1
    assert target.error_kinds == {"timeout": 1}


async def test_error_rate_null_without_finished_results(
    session_factory: Factory,
) -> None:
    experiment_id = await seed(
        session_factory, [Row(1, status="pending"), Row(2, status="cancelled")]
    )
    comp = await _comparison(session_factory, experiment_id)
    assert comp.targets[0].error_rate is None
    assert comp.finished is False


async def test_two_targets_and_target_without_results(
    session_factory: Factory,
) -> None:
    rows = [Row(p, model="m1", latency=p * 100) for p in (1, 2, 3)]
    rows += [Row(p, model="m2", latency=1000) for p in (1, 2)]
    experiment_id = await seed(session_factory, rows, models=("m1", "m2", "m3"))
    comp = await _comparison(session_factory, experiment_id)
    by_model = {t.model: t for t in comp.targets}
    assert [t.model for t in comp.targets] == ["m1", "m2", "m3"]
    assert by_model["m1"].counts.total == 3
    assert by_model["m2"].counts.total == 2
    assert by_model["m1"].latency.max_ms == 300
    ghost = by_model["m3"]
    assert ghost.counts.total == 0
    assert ghost.error_rate is None
    assert ghost.latency.count == 0 and ghost.latency.mean_ms is None
    assert ghost.tokens.input_tokens.total is None
    assert ghost.cost.estimated_cost is None


async def test_latency_stats(session_factory: Factory) -> None:
    rows = [Row(p, latency=p * 100.0) for p in (1, 2, 3)]
    experiment_id = await seed(session_factory, rows)
    comp = await _comparison(session_factory, experiment_id)
    lat = comp.targets[0].latency
    assert (lat.count, lat.min_ms, lat.mean_ms) == (3, 100, 200)
    assert (lat.median_ms, lat.max_ms) == (200, 300)


async def test_partial_token_coverage(session_factory: Factory) -> None:
    rows = [
        Row(1, tokens=(10, 1, 11)),
        Row(2, tokens=(20, 2, 22)),
        Row(3),
    ]
    experiment_id = await seed(session_factory, rows)
    tokens = (
        (await _comparison(session_factory, experiment_id)).targets[0].tokens
    )
    assert tokens.succeeded == 3
    assert tokens.input_tokens.total == 30
    assert tokens.input_tokens.reported_count == 2


async def test_pass_rate(session_factory: Factory) -> None:
    rows = [Row(p, evals={"a": "passed"}) for p in (1, 2, 3)]
    rows += [Row(4, evals={"a": "failed"})]
    rows += [Row(p, evals={"a": "skipped"}) for p in (5, 6)]
    experiment_id = await seed(session_factory, rows, evaluators=("a", "b"))
    evaluators = (
        (await _comparison(session_factory, experiment_id))
        .targets[0]
        .evaluators
    )
    a, b = evaluators
    assert (a.id, a.passed, a.failed, a.skipped) == ("a", 3, 1, 2)
    assert a.pass_rate == 0.75
    assert (b.id, b.passed, b.pass_rate) == ("b", 0, None)


async def test_cost_example(session_factory: Factory, tmp_path: Path) -> None:
    rows = [Row(1, tokens=(1_000_000, 500_000, 1_500_000))]
    experiment_id = await seed(session_factory, rows)
    comp = await _comparison(session_factory, experiment_id, _pricing(tmp_path))
    cost = comp.targets[0].cost
    assert cost.priced is True
    assert cost.estimated_cost == Decimal("2.00")
    assert cost.pricing_version == "p-1" and cost.currency == "USD"
    assert cost.rates is not None
    assert cost.rates.input_per_million == Decimal("1.00")
    assert (cost.results_included, cost.results_excluded) == (1, 0)
    assert comp.pricing.version == "p-1"
    assert comp.pricing_error is None


async def test_cost_counts_excluded_results(
    session_factory: Factory, tmp_path: Path
) -> None:
    rows = [
        Row(1, tokens=(1_000_000, 0, 1)),
        Row(2),
        Row(3, tokens=(5, None, 5)),
    ]
    experiment_id = await seed(session_factory, rows)
    comp = await _comparison(session_factory, experiment_id, _pricing(tmp_path))
    cost = comp.targets[0].cost
    assert cost.estimated_cost == Decimal("1.00")
    assert (cost.results_included, cost.results_excluded) == (1, 2)


async def test_cost_null_when_no_result_has_tokens(
    session_factory: Factory, tmp_path: Path
) -> None:
    experiment_id = await seed(session_factory, [Row(1)])
    comp = await _comparison(session_factory, experiment_id, _pricing(tmp_path))
    cost = comp.targets[0].cost
    assert cost.priced is True and cost.estimated_cost is None
    assert cost.results_included == 0


async def test_unpriced_model(session_factory: Factory, tmp_path: Path) -> None:
    rows = [Row(1, model="m2", tokens=(10, 10, 20))]
    experiment_id = await seed(session_factory, rows, models=("m2",))
    comp = await _comparison(session_factory, experiment_id, _pricing(tmp_path))
    cost = comp.targets[0].cost
    assert cost.priced is False
    assert cost.estimated_cost is None and cost.rates is None
    assert comp.pricing.version == "p-1"


async def test_no_pricing_file(session_factory: Factory) -> None:
    rows = [Row(1, tokens=(10, 10, 20))]
    experiment_id = await seed(session_factory, rows)
    comp = await _comparison(session_factory, experiment_id, None)
    assert comp.targets[0].cost.estimated_cost is None
    assert comp.pricing.version is None and comp.pricing.currency is None
    assert comp.pricing_error is None


async def test_invalid_pricing_keeps_other_metrics(
    session_factory: Factory, tmp_path: Path
) -> None:
    rows = [Row(1, latency=10, tokens=(10, 10, 20)), Row(2, status="failed")]
    experiment_id = await seed(session_factory, rows)
    path = _pricing(tmp_path, input_rate=-1)
    comp = await _comparison(session_factory, experiment_id, path)
    assert comp.pricing_error is not None and path in comp.pricing_error
    target = comp.targets[0]
    assert target.cost.estimated_cost is None
    assert target.error_rate == 0.5
    assert target.latency.max_ms == 10
    assert target.tokens.input_tokens.total == 10

    missing = await _comparison(
        session_factory, experiment_id, str(tmp_path / "missing.json")
    )
    assert missing.pricing_error is not None
    assert missing.targets[0].error_rate == 0.5


async def test_cases_view_without_and_with_responses(
    session_factory: Factory,
) -> None:
    rows = [
        Row(
            p,
            model=m,
            text=f"resp-{p}-{m}",
            latency=5,
            tokens=(1, 2, 3),
            evals={"a": "passed"},
        )
        for p in (1, 2, 3)
        for m in ("m1", "m2")
    ]
    experiment_id = await seed(
        session_factory, rows, models=("m1", "m2"), evaluators=("a",)
    )
    async with session_factory() as session:
        service = MetricsService(session)
        page = await service.cases(ROOT, experiment_id, limit=2, offset=0)
        assert (page.total, page.limit, page.offset) == (3, 2, 0)
        assert [c.case_position for c in page.items] == [1, 2]
        for case in page.items:
            assert [r.model for r in case.results] == ["m1", "m2"]
            assert all(r.response_text is None for r in case.results)
            assert case.results[0].evaluations[0].status == "passed"
            assert case.results[0].input_tokens == 1
        full = await service.cases(
            ROOT, experiment_id, limit=50, offset=2, include_responses=True
        )
        assert [c.case_position for c in full.items] == [3]
        assert [r.response_text for r in full.items[0].results] == [
            "resp-3-m1",
            "resp-3-m2",
        ]
        beyond = await service.cases(ROOT, experiment_id, limit=5, offset=10)
        assert beyond.items == [] and beyond.total == 3


async def test_unknown_experiment(session_factory: Factory) -> None:
    async with session_factory() as session:
        service = MetricsService(session)
        missing = uuid.uuid4()
        with pytest.raises(ExperimentNotFoundError):
            await service.summary(ROOT, missing)
        with pytest.raises(ExperimentNotFoundError):
            await service.comparison(ROOT, missing)
        with pytest.raises(ExperimentNotFoundError):
            await service.cases(ROOT, missing, 10, 0)
