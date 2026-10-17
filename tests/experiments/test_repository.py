"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.datasets.models import Dataset, TestCase
from components.experiments.models import (
    Experiment,
    ExperimentResult,
    ExperimentStatus,
    ResultStatus,
)
from components.experiments.repository import (
    ExperimentRepository,
    ExperimentResultRepository,
)
from tests.access import ROOT, seed_organization

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


async def _experiment(
    session: AsyncSession, cases: int = 3, targets: tuple[str, ...] = ("m1",)
) -> tuple[Experiment, list[ExperimentResult]]:
    dataset = Dataset(
        name="d", organization_id=await seed_organization(session)
    )
    session.add(dataset)
    await session.flush()
    case_rows = [
        TestCase(dataset_id=dataset.id, position=p, input=f"i{p}")
        for p in range(1, cases + 1)
    ]
    session.add_all(case_rows)
    experiment = Experiment(
        name="e",
        dataset_id=dataset.id,
        organization_id=dataset.organization_id,
        prompt_template="{input}",
        config={"targets": []},
        evaluator_config={},
    )
    session.add(experiment)
    await session.flush()
    results = [
        ExperimentResult(
            experiment_id=experiment.id,
            test_case_id=case.id,
            case_position=case.position,
            provider="openai",
            model=model,
            rendered_prompt=case.input,
        )
        # Insert in reverse so ordering cannot be insertion order.
        for case in reversed(case_rows)
        for model in reversed(targets)
    ]
    await ExperimentResultRepository(session).add_many(results)
    return experiment, results


async def test_defaults_applied(session_factory: Factory) -> None:
    async with session_factory() as s:
        experiment, results = await _experiment(s, cases=1)
        await s.commit()
        await s.refresh(experiment)
        await s.refresh(results[0])
        assert experiment.status == ExperimentStatus.PENDING
        assert experiment.cancel_requested is False
        assert experiment.created_at is not None
        assert results[0].status == ResultStatus.PENDING
        assert results[0].attempts == 0
        assert results[0].input_tokens is None
        assert results[0].latency_ms is None


async def test_result_page_ordering_pagination_and_filter(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, results = await _experiment(s, cases=3, targets=("b", "a"))
        repo = ExperimentResultRepository(s)
        page1, total = await repo.list_page(experiment.id, limit=4, offset=0)
        page2, _ = await repo.list_page(experiment.id, limit=4, offset=4)
        assert total == 6
        keys = [(r.case_position, r.model) for r in [*page1, *page2]]
        assert keys == [
            (1, "a"),
            (1, "b"),
            (2, "a"),
            (2, "b"),
            (3, "a"),
            (3, "b"),
        ]
        assert (len(page1), len(page2)) == (4, 2)
        empty, total = await repo.list_page(experiment.id, limit=4, offset=10)
        assert empty == [] and total == 6

        results[0].status = ResultStatus.FAILED.value
        await s.flush()
        failed, total = await repo.list_page(
            experiment.id, 10, 0, status=ResultStatus.FAILED.value
        )
        assert total == 1 and failed[0].id == results[0].id
        none, total = await repo.list_page(
            experiment.id, 10, 0, status=ResultStatus.SUCCEEDED.value
        )
        assert none == [] and total == 0


async def test_page_is_scoped_to_experiment(session_factory: Factory) -> None:
    async with session_factory() as s:
        first, _ = await _experiment(s, cases=2)
        second, _ = await _experiment(s, cases=1)
        repo = ExperimentResultRepository(s)
        assert (await repo.list_page(first.id, 10, 0))[1] == 2
        assert (await repo.list_page(second.id, 10, 0))[1] == 1


async def test_experiment_page_ordering(session_factory: Factory) -> None:
    async with session_factory() as s:
        for _ in range(3):
            await _experiment(s, cases=1)
        repo = ExperimentRepository(s)
        items, total = await repo.list_page(ROOT, limit=2, offset=0)
        rest, _ = await repo.list_page(ROOT, limit=2, offset=2)
        keys = [(e.created_at, e.id) for e in [*items, *rest]]
        assert total == 3 and keys == sorted(keys) and len(set(keys)) == 3


async def test_status_counts_and_non_terminal(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, results = await _experiment(s, cases=5)
        repo = ExperimentResultRepository(s)
        assert await repo.status_counts(experiment.id) == {"pending": 5}
        statuses = ["succeeded", "succeeded", "failed", "running", "pending"]
        for row, status in zip(results, statuses, strict=True):
            row.status = status
        await s.flush()
        assert await repo.status_counts(experiment.id) == {
            "succeeded": 2,
            "failed": 1,
            "running": 1,
            "pending": 1,
        }
        assert await repo.count_non_terminal(experiment.id) == 2
        for row in results:
            row.status = "cancelled"
        await s.flush()
        assert await repo.count_non_terminal(experiment.id) == 0
        assert await repo.status_counts(uuid.uuid4()) == {}


async def test_unique_result_per_case_and_target(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        _, results = await _experiment(s, cases=1)
        dup = results[0]
        s.add(
            ExperimentResult(
                experiment_id=dup.experiment_id,
                test_case_id=dup.test_case_id,
                case_position=1,
                provider=dup.provider,
                model=dup.model,
                rendered_prompt="x",
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()


async def test_add_many_batches(session_factory: Factory) -> None:
    async with session_factory() as s:
        experiment, _ = await _experiment(s, cases=1)
        case_id = await s.scalar(select(TestCase.id))
        assert case_id is not None
        extra = [
            ExperimentResult(
                experiment_id=experiment.id,
                test_case_id=case_id,
                case_position=1,
                provider="openai",
                model=f"x{i}",
                rendered_prompt="p",
            )
            for i in range(1100)
        ]
        await ExperimentResultRepository(s).add_many(extra)
        total = await s.scalar(
            select(func.count()).select_from(ExperimentResult)
        )
        assert total == 1101


async def test_claim_pending_sets_running_and_counts_attempt(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        _, results = await _experiment(s, cases=1)
        await s.commit()
        repo = ExperimentResultRepository(s)
        claimed = await repo.claim(results[0].id)
        assert claimed is not None
        assert claimed.status == ResultStatus.RUNNING
        assert claimed.attempts == 1
        assert claimed.started_at is not None
        await s.commit()
        # Fresh claim is held: a second claim is a no-op.
        assert await repo.claim(results[0].id) is None


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
async def test_claim_ignores_finished_results(
    session_factory: Factory, status: str
) -> None:
    async with session_factory() as s:
        _, results = await _experiment(s, cases=1)
        results[0].status = status
        await s.commit()
        assert await ExperimentResultRepository(s).claim(results[0].id) is None
        await s.refresh(results[0])
        assert results[0].status == status and results[0].attempts == 0


async def test_claim_unknown_id(session_factory: Factory) -> None:
    async with session_factory() as s:
        assert await ExperimentResultRepository(s).claim(uuid.uuid4()) is None


async def test_concurrent_claims_grant_exactly_one(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        _, results = await _experiment(s, cases=1)
        await s.commit()
        result_id = results[0].id

    async def attempt() -> bool:
        async with session_factory() as session:
            claimed = await ExperimentResultRepository(session).claim(result_id)
            await session.commit()
            return claimed is not None

    outcomes = await asyncio.gather(*(attempt() for _ in range(8)))
    assert outcomes.count(True) == 1
    async with session_factory() as s:
        row = await s.get(ExperimentResult, result_id)
        assert row is not None and row.attempts == 1


async def test_stale_lease_is_reclaimed(session_factory: Factory) -> None:
    async with session_factory() as s:
        _, results = await _experiment(s, cases=2)
        stale, fresh = results[0], results[1]
        now = await s.scalar(select(func.now()))
        assert now is not None
        for row, age in ((stale, 2000), (fresh, 100)):
            row.status = "running"
            row.attempts = 1
            row.started_at = now - timedelta(seconds=age)
        await s.commit()
        repo = ExperimentResultRepository(s)
        assert await repo.claim(fresh.id) is None
        claimed = await repo.claim(stale.id)
        assert claimed is not None
        assert claimed.attempts == 2 and claimed.status == "running"
        # Lease is renewed by the reclaim.
        await s.commit()
        assert await repo.claim(stale.id) is None
        # A shorter explicit lease makes the fresh one reclaimable.
        assert await repo.claim(fresh.id, lease_seconds=50) is not None


async def test_write_outcome_only_while_running(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        _, results = await _experiment(s, cases=1)
        await s.commit()
        repo = ExperimentResultRepository(s)
        fields = {"response_text": "hi", "input_tokens": 3, "latency_ms": 1.5}
        # Not claimed yet: dropped.
        assert not await repo.write_outcome(
            results[0].id, ResultStatus.SUCCEEDED, fields, datetime.now(UTC)
        )
        claim = await repo.claim(results[0].id)
        assert claim is not None and claim.started_at is not None
        started = claim.started_at
        assert await repo.write_outcome(
            results[0].id, ResultStatus.SUCCEEDED, fields, started
        )
        await s.commit()
        row = await s.get(
            ExperimentResult, results[0].id, populate_existing=True
        )
        assert row is not None
        assert row.status == "succeeded" and row.response_text == "hi"
        assert row.input_tokens == 3 and row.output_tokens is None
        assert row.finished_at is not None
        # A late write from a reclaimed stale worker must not overwrite.
        assert not await repo.write_outcome(
            results[0].id,
            ResultStatus.FAILED,
            {"error_kind": "timeout"},
            started,
        )
        await s.refresh(row)
        assert row.status == "succeeded" and row.error_kind is None


async def test_get_for_update_blocks_second_locker(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, _ = await _experiment(s, cases=1)
        await s.commit()
        exp_id = experiment.id

    async with session_factory() as first, session_factory() as second:
        locked = await ExperimentRepository(first).get_for_update_unscoped(
            exp_id
        )
        assert locked is not None
        await second.execute(text("SET lock_timeout = '300ms'"))
        with pytest.raises(Exception, match="lock"):
            await ExperimentRepository(second).get_for_update_unscoped(exp_id)
        await second.rollback()
        await first.commit()
        again = await ExperimentRepository(second).get_for_update_unscoped(
            exp_id
        )
        assert again is not None
        assert (
            await ExperimentRepository(first).get_for_update_unscoped(
                uuid.uuid4()
            )
            is None
        )


async def test_deleting_experiment_cascades_to_results(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, _ = await _experiment(s, cases=2)
        await s.commit()
        await s.delete(experiment)
        await s.commit()
        assert (
            await s.scalar(select(func.count()).select_from(ExperimentResult))
            == 0
        )
        # Dataset and cases are untouched.
        assert await s.scalar(select(func.count()).select_from(TestCase)) == 2


async def test_cases_and_dataset_are_protected_by_results(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, _ = await _experiment(s, cases=1)
        await s.commit()
        experiment_id = experiment.id  # rollback expires loaded attributes
        with pytest.raises(IntegrityError):
            await s.execute(text("DELETE FROM test_cases"))
        await s.rollback()
        with pytest.raises(IntegrityError):
            await s.execute(text("DELETE FROM datasets"))
        await s.rollback()
        assert await s.get(Experiment, experiment_id) is not None


async def test_list_ids_cancel_pending_and_reset_failed(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        experiment, results = await _experiment(s, cases=4)
        await s.commit()
        repo = ExperimentResultRepository(s)
        ordered = await repo.list_ids_by_status(
            experiment.id, ResultStatus.PENDING
        )
        by_id = {r.id: r for r in results}
        assert [by_id[i].case_position for i in ordered] == [1, 2, 3, 4]
        by_id[ordered[0]].status = "failed"
        by_id[ordered[0]].attempts = 2
        by_id[ordered[0]].error_kind = "timeout"
        by_id[ordered[1]].status = "running"
        by_id[ordered[2]].status = "succeeded"
        await s.flush()
        assert await repo.cancel_pending(experiment.id) == 1
        assert await repo.status_counts(experiment.id) == {
            "failed": 1,
            "running": 1,
            "succeeded": 1,
            "cancelled": 1,
        }
        assert await repo.reset_failed(experiment.id) == [ordered[0]]
        await s.refresh(by_id[ordered[0]])
        row = by_id[ordered[0]]
        assert (row.status, row.attempts, row.error_kind) == (
            "pending",
            0,
            None,
        )
        assert by_id[ordered[2]].status == "succeeded"


async def test_late_write_from_reclaimed_claim_is_rejected(
    session_factory: Factory,
) -> None:
    async with session_factory() as s:
        _, results = await _experiment(s, cases=1)
        await s.commit()
        repo = ExperimentResultRepository(s)
        first = await repo.claim(results[0].id)
        assert first is not None and first.started_at is not None
        first_started = first.started_at
        await s.commit()
        # The lease expires, a second worker claims the same result.
        await s.execute(
            text(
                "update experiment_results "
                "set started_at = now() - interval '1 hour'"
            )
        )
        await s.commit()
        second = await repo.claim(results[0].id)
        assert second is not None and second.started_at is not None
        assert second.started_at != first_started
        await s.commit()
        # The first worker finishes late: dropped, row still running.
        assert not await repo.write_outcome(
            results[0].id,
            ResultStatus.SUCCEEDED,
            {"response_text": "late"},
            first_started,
        )
        await s.commit()
        row = await s.get(
            ExperimentResult, results[0].id, populate_existing=True
        )
        assert row is not None
        assert row.status == "running" and row.response_text is None
        assert await repo.write_outcome(
            results[0].id,
            ResultStatus.SUCCEEDED,
            {"response_text": "new"},
            second.started_at,
        )


async def test_list_stale_running_ids(session_factory: Factory) -> None:
    async with session_factory() as s:
        experiment, results = await _experiment(s, cases=3)
        for row in results[:2]:
            row.status = "running"
        await s.flush()
        await s.execute(
            text(
                "update experiment_results set started_at = now() - "
                "interval '1 hour' where id = :id"
            ),
            {"id": results[0].id},
        )
        await s.execute(
            text(
                "update experiment_results set started_at = now() "
                "where id = :id"
            ),
            {"id": results[1].id},
        )
        await s.commit()
        repo = ExperimentResultRepository(s)
        assert await repo.list_stale_running_ids(experiment.id) == [
            results[0].id
        ]
