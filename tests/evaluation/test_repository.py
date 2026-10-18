"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.schemas import Principal
from components.datasets.models import Dataset, TestCase
from components.evaluation.models import EvaluationResult
from components.evaluation.repository import EvaluationRepository
from components.experiments.enums import ResultStatus
from components.experiments.models import Experiment, ExperimentResult
from tests.access import seed_organization

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


async def _setup(
    session: AsyncSession,
    statuses: tuple[ResultStatus, ...] = (
        ResultStatus.SUCCEEDED,
        ResultStatus.SUCCEEDED,
        ResultStatus.FAILED,
    ),
    evaluator_config: dict[str, Any] | None = None,
    owner: Principal | None = None,
) -> tuple[Experiment, list[ExperimentResult]]:
    """Seed an experiment; without ``owner`` it has no owner (legacy)."""
    dataset = Dataset(
        name="d",
        organization_id=(
            owner.organization_id
            if owner and owner.organization_id
            else await seed_organization(session)
        ),
        owner_id=owner.user_id if owner else None,
    )
    session.add(dataset)
    await session.flush()
    cases = [
        TestCase(
            dataset_id=dataset.id,
            position=p,
            input=f"i{p}",
            expected_output=None if p == 1 else f"out{p}",
        )
        for p in range(1, len(statuses) + 1)
    ]
    session.add_all(cases)
    experiment = Experiment(
        name="e",
        dataset_id=dataset.id,
        organization_id=dataset.organization_id,
        owner_id=dataset.owner_id,
        prompt_template="{input}",
        config={"targets": []},
        evaluator_config=evaluator_config or {},
    )
    session.add(experiment)
    await session.flush()
    results = [
        ExperimentResult(
            experiment_id=experiment.id,
            test_case_id=case.id,
            case_position=case.position,
            provider="openai",
            model="m",
            rendered_prompt=case.input,
            status=status.value,
            response_text=f"r{case.position}",
        )
        for case, status in zip(cases, statuses, strict=True)
    ]
    session.add_all(results)
    await session.flush()
    return experiment, results


def _row(
    experiment: Experiment,
    result: ExperimentResult,
    evaluator_id: str = "e1",
    status: str = "passed",
) -> dict[str, Any]:
    return {
        "experiment_result_id": result.id,
        "experiment_id": experiment.id,
        "evaluator_id": evaluator_id,
        "evaluator_type": "exact_match",
        "evaluator_version": 1,
        "status": status,
        "details": {"reason": "match"},
    }


async def _count(session: AsyncSession) -> int:
    return (
        await session.scalar(select(func.count()).select_from(EvaluationResult))
    ) or 0


async def test_insert_ignore_is_idempotent_and_keeps_existing(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, results = await _setup(s)
        repo = EvaluationRepository(s)
        assert await repo.insert_ignore([_row(experiment, results[0])]) == 1
        await s.commit()
    async with session_factory() as s:
        repo = EvaluationRepository(s)
        changed = _row(experiment, results[0], status="failed")
        other = _row(experiment, results[0], evaluator_id="e2")
        assert await repo.insert_ignore([changed, other]) == 1
        assert await repo.insert_ignore([changed, other]) == 0
        assert await repo.insert_ignore([]) == 0
        await s.commit()
        rows = (await s.scalars(select(EvaluationResult))).all()
        by_id = {r.evaluator_id: r for r in rows}
        assert len(rows) == 2
        assert by_id["e1"].status == "passed"
        assert by_id["e1"].details == {"reason": "match"}
        assert by_id["e1"].created_at is not None


async def test_insert_ignore_duplicates_within_one_batch(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, results = await _setup(s)
        row = _row(experiment, results[0])
        assert await EvaluationRepository(s).insert_ignore([row, row]) == 1
        assert await _count(s) == 1


async def test_list_page_order_pagination_and_filters(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, results = await _setup(s)
        repo = EvaluationRepository(s)
        rows = [
            _row(experiment, r, ev, st)
            for r, st in zip(
                reversed(results[:2]), ("failed", "passed"), strict=True
            )
            for ev in ("b", "a")
        ]
        await repo.insert_ignore(rows)
        page1, total = await repo.list_page(experiment.id, 3, 0)
        page2, _ = await repo.list_page(experiment.id, 3, 3)
        assert total == 4
        keys = [
            (r.experiment_result_id, r.evaluator_id) for r in [*page1, *page2]
        ]
        assert keys == [
            (results[0].id, "a"),
            (results[0].id, "b"),
            (results[1].id, "a"),
            (results[1].id, "b"),
        ]
        only_a, total_a = await repo.list_page(
            experiment.id, 10, 0, evaluator_id="a"
        )
        assert total_a == 2 and {r.evaluator_id for r in only_a} == {"a"}
        failed, total_f = await repo.list_page(
            experiment.id, 10, 0, status="failed"
        )
        assert total_f == 2 and {r.status for r in failed} == {"failed"}
        both, total_b = await repo.list_page(
            experiment.id, 10, 0, evaluator_id="a", status="passed"
        )
        assert total_b == 1 and len(both) == 1
        none, total_n = await repo.list_page(uuid.uuid4(), 10, 0)
        assert total_n == 0 and none == []


async def test_outcome_counts_per_evaluator(session_factory: Factory) -> None:
    async with session_factory() as s:
        experiment, results = await _setup(s)
        other, other_results = await _setup(s)
        repo = EvaluationRepository(s)
        await repo.insert_ignore(
            [
                _row(experiment, results[0], "exact", "passed"),
                _row(experiment, results[1], "exact", "passed"),
                _row(experiment, results[2], "exact", "failed"),
                _row(experiment, results[0], "json", "skipped"),
                _row(other, other_results[0], "exact", "passed"),
            ]
        )
        assert await repo.outcome_counts(experiment.id) == {
            "exact": {"passed": 2, "failed": 1},
            "json": {"skipped": 1},
        }
        assert await repo.outcome_counts(uuid.uuid4()) == {}


async def test_unevaluated_succeeded_results(session_factory: Factory) -> None:
    async with session_factory() as s:
        experiment, results = await _setup(s)
        repo = EvaluationRepository(s)
        # results[0] has both, results[1] only one, results[2] failed.
        await repo.insert_ignore(
            [
                _row(experiment, results[0], "a"),
                _row(experiment, results[0], "b"),
                _row(experiment, results[1], "a"),
                # Not configured any more: must not count as evaluated.
                _row(experiment, results[1], "retired"),
            ]
        )
        assert await repo.list_unevaluated_result_ids(
            experiment.id, ["a", "b"]
        ) == [results[1].id]
        assert await repo.list_unevaluated_result_ids(
            experiment.id, ["a", "b", "c"]
        ) == [results[0].id, results[1].id]
        assert (
            await repo.list_unevaluated_result_ids(experiment.id, ["a"]) == []
        )
        assert await repo.list_unevaluated_result_ids(experiment.id, []) == []
        assert (
            await repo.list_unevaluated_result_ids(experiment.id, ["a", "a"])
            == []
        )


async def test_unevaluated_lists_all_when_nothing_stored(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, results = await _setup(s)
        ids = await EvaluationRepository(s).list_unevaluated_result_ids(
            experiment.id, ["a"]
        )
        assert ids == [results[0].id, results[1].id]


async def test_get_input_joins_case_and_experiment_config(
    session_factory: Factory,
) -> None:
    config = {"evaluators": [{"id": "e1", "type": "json_valid"}]}
    async with session_factory() as s:
        experiment, results = await _setup(s, evaluator_config=config)
        repo = EvaluationRepository(s)
        first = await repo.get_input(results[0].id)
        second = await repo.get_input(results[1].id)
        failed = await repo.get_input(results[2].id)
        assert first is not None and second is not None and failed is not None
        assert first.expected_output is None
        assert second.expected_output == "out2"
        assert second.response_text == "r2"
        assert second.experiment_id == experiment.id
        assert second.status == "succeeded"
        assert second.evaluator_config == config
        assert failed.status == "failed"
        assert await repo.get_input(uuid.uuid4()) is None


async def test_cascade_delete_with_result_and_experiment(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, results = await _setup(s)
        repo = EvaluationRepository(s)
        await repo.insert_ignore(
            [_row(experiment, results[0]), _row(experiment, results[1])]
        )
        await s.commit()
        await s.delete(results[0])
        await s.commit()
        assert await _count(s) == 1
        await s.delete(experiment)
        await s.commit()
        assert await _count(s) == 0
