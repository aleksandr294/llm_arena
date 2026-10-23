"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import asyncio
import logging
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from components.evaluation import evaluators
from components.evaluation.models import EvaluationResult
from components.evaluation.repository import EvaluationRepository
from components.experiments.enums import ResultStatus
from components.experiments.exceptions import ExperimentNotFoundError
from components.experiments.models import ExperimentResult
from tests.access import ROOT
from tests.evaluation.helpers import ScopedEvaluation
from tests.evaluation.test_repository import Factory, _setup

pytestmark = pytest.mark.integration

CONFIG: dict[str, Any] = {
    "evaluators": [
        {"id": "exact", "type": "exact_match"},
        {"id": "has_r", "type": "contains", "values": ["r"]},
    ]
}


async def _make(
    factory: Factory,
    config: dict[str, Any] | None = CONFIG,
    **kwargs: Any,
) -> list[uuid.UUID]:
    async with factory() as session:
        _, results = await _setup(session, evaluator_config=config, **kwargs)
        await session.commit()
        return [r.id for r in results]


async def _rows(factory: Factory) -> list[EvaluationResult]:
    async with factory() as session:
        found = await session.scalars(
            select(EvaluationResult).order_by(EvaluationResult.evaluator_id)
        )
        return list(found.all())


async def test_two_evaluators_store_two_rows(session_factory: Factory) -> None:
    ids = await _make(session_factory)
    service = ScopedEvaluation(session_factory)
    assert await service.evaluate_result(ids[1]) == 2
    rows = await _rows(session_factory)
    assert [(r.evaluator_id, r.status) for r in rows] == [
        ("exact", "failed"),
        ("has_r", "passed"),
    ]
    assert rows[0].evaluator_type == "exact_match"
    assert rows[0].evaluator_version == 1
    assert all(r.experiment_result_id == ids[1] for r in rows)


async def test_missing_expected_output_is_skipped(
    session_factory: Factory,
) -> None:
    ids = await _make(session_factory)
    await ScopedEvaluation(session_factory).evaluate_result(ids[0])
    statuses = {r.evaluator_id: r.status for r in await _rows(session_factory)}
    assert statuses["exact"] == "skipped"


async def test_failed_generation_is_not_evaluated(
    session_factory: Factory,
) -> None:
    ids = await _make(session_factory)
    assert await ScopedEvaluation(session_factory).evaluate_result(ids[2]) == 0
    assert await _rows(session_factory) == []


async def test_unknown_result_is_ignored(session_factory: Factory) -> None:
    service = ScopedEvaluation(session_factory)
    assert await service.evaluate_result(uuid.uuid4()) == 0


async def test_duplicate_delivery_keeps_one_row_each(
    session_factory: Factory,
) -> None:
    ids = await _make(session_factory)
    service = ScopedEvaluation(session_factory)
    assert await service.evaluate_result(ids[1]) == 2
    assert await service.evaluate_result(ids[1]) == 0
    assert len(await _rows(session_factory)) == 2


async def test_evaluator_exception_is_stored_as_error(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _make(session_factory)

    def boom(self: Any, response_text: str, expected: str | None) -> Any:
        raise RuntimeError("secret r1 text")

    monkeypatch.setattr(evaluators.ContainsEvaluator, "evaluate", boom)
    await ScopedEvaluation(session_factory).evaluate_result(ids[1])
    rows = {r.evaluator_id: r for r in await _rows(session_factory)}
    assert rows["has_r"].status == "error"
    assert rows["has_r"].details == {
        "reason": "evaluator_exception",
        "exception": "RuntimeError",
    }
    assert rows["exact"].status == "failed"  # others are unaffected


@pytest.mark.parametrize(
    "config", [{"checks": [1]}, {"evaluators": [{"type": "nope"}]}, {}]
)
async def test_unparsable_or_empty_config_means_no_evaluation(
    session_factory: Factory,
    config: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    ids = await _make(session_factory, config=config)
    with caplog.at_level(logging.WARNING):
        assert (
            await ScopedEvaluation(session_factory).evaluate_result(ids[1]) == 0
        )
    assert await _rows(session_factory) == []
    if config:
        assert "unparsable" in caplog.text


async def test_generation_state_is_unchanged(session_factory: Factory) -> None:
    ids = await _make(session_factory)
    async with session_factory() as s:
        before = [
            (r.status, r.response_text, r.attempts)
            for r in await s.scalars(
                select(ExperimentResult).order_by(ExperimentResult.id)
            )
        ]
    service = ScopedEvaluation(session_factory)
    for result_id in ids:
        await service.evaluate_result(result_id)
    async with session_factory() as s:
        after = [
            (r.status, r.response_text, r.attempts)
            for r in await s.scalars(
                select(ExperimentResult).order_by(ExperimentResult.id)
            )
        ]
    assert after == before
    assert ResultStatus.SUCCEEDED.value in {status for status, *_ in after}


async def test_backfill_and_summary(session_factory: Factory) -> None:
    ids = await _make(session_factory)
    service = ScopedEvaluation(session_factory)
    async with session_factory() as s:
        experiment_id = (await s.get(ExperimentResult, ids[0])).experiment_id  # type: ignore[union-attr]

    backlog, remaining = await service.backfill(ROOT, experiment_id)
    assert set(backlog) == {ids[0], ids[1]} and remaining == 0
    summary = await service.summary(ROOT, experiment_id)
    assert summary.unevaluated_results == 2
    assert next(e.model_dump() for e in summary.evaluators) == {
        "id": "exact",
        "type": "exact_match",
        "version": 1,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "error": 0,
    }

    await service.evaluate_result(ids[1])
    assert await service.backfill(ROOT, experiment_id) == ([ids[0]], 0)
    summary = await service.summary(ROOT, experiment_id)
    assert summary.unevaluated_results == 1
    by_id = {e.id: e for e in summary.evaluators}
    assert (by_id["exact"].failed, by_id["has_r"].passed) == (1, 1)


async def test_backfill_without_evaluators_is_empty(
    session_factory: Factory,
) -> None:
    ids = await _make(session_factory, config={})
    service = ScopedEvaluation(session_factory)
    async with session_factory() as s:
        experiment_id = (await s.get(ExperimentResult, ids[0])).experiment_id  # type: ignore[union-attr]
    assert await service.backfill(ROOT, experiment_id) == ([], 0)
    summary = await service.summary(ROOT, experiment_id)
    assert summary.evaluators == []
    assert summary.unevaluated_results == 0


async def test_unknown_experiment_raises(session_factory: Factory) -> None:
    service = ScopedEvaluation(session_factory)
    with pytest.raises(ExperimentNotFoundError):
        await service.backfill(ROOT, uuid.uuid4())
    with pytest.raises(ExperimentNotFoundError):
        await service.summary(ROOT, uuid.uuid4())


async def test_backfill_is_capped_and_reports_remaining(
    session_factory: Factory,
) -> None:
    ids = await _make(session_factory)
    service = ScopedEvaluation(session_factory)
    async with session_factory() as s:
        experiment_id = (await s.get(ExperimentResult, ids[0])).experiment_id  # type: ignore[union-attr]
    assert await service.backfill(ROOT, experiment_id, limit=1) == ([ids[0]], 1)


async def test_summary_counts_without_listing_ids(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _make(session_factory)
    async with session_factory() as s:
        experiment_id = (await s.get(ExperimentResult, ids[0])).experiment_id  # type: ignore[union-attr]

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("summary must not load ids")

    monkeypatch.setattr(
        EvaluationRepository, "list_unevaluated_result_ids", boom
    )
    summary = await ScopedEvaluation(session_factory).summary(
        ROOT, experiment_id
    )
    assert summary.unevaluated_results == 2


async def test_succeeded_result_without_response_is_stored_as_error(
    session_factory: Factory,
) -> None:
    ids = await _make(session_factory)
    async with session_factory() as s:
        result = await s.get(ExperimentResult, ids[0])
        assert result is not None
        result.response_text = None
        await s.commit()
    service = ScopedEvaluation(session_factory)
    assert await service.evaluate_result(ids[0]) == 2
    rows = await _rows(session_factory)
    assert [(r.status, r.details) for r in rows] == [
        ("error", {"reason": "missing_response"})
    ] * 2
    assert await service.backfill(
        ROOT, await _experiment_id(session_factory, ids[0])
    ) == ([ids[1]], 0)


async def _experiment_id(factory: Factory, result_id: uuid.UUID) -> uuid.UUID:
    async with factory() as s:
        result = await s.get(ExperimentResult, result_id)
        assert result is not None
        return result.experiment_id


async def test_concurrent_evaluation_stores_one_row_per_evaluator(
    session_factory: Factory,
) -> None:
    ids = await _make(session_factory)
    service = ScopedEvaluation(session_factory)
    inserted = await asyncio.gather(
        service.evaluate_result(ids[1]), service.evaluate_result(ids[1])
    )
    assert sum(inserted) == 2
    rows = await _rows(session_factory)
    assert [r.evaluator_id for r in rows] == ["exact", "has_r"]
