"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from components.core.enums import Role
from components.core.exceptions import OrganizationRequiredError
from components.datasets.models import TestCase
from components.datasets.service import DatasetNotFoundError
from components.experiments.models import (
    Experiment,
    ExperimentResult,
    ExperimentStatus,
    ResultStatus,
)
from components.experiments.repository import ExperimentResultRepository
from components.experiments.service import (
    ExperimentNotFoundError,
    ExperimentService,
    ExperimentValidationError,
    InvalidTransitionError,
)
from components.third_party.llm.schemas import ProviderName
from tests.access import ROOT, seed_principal
from tests.experiments.helpers import (
    Enqueued,
    Factory,
    FakeLLM,
    create_data,
    make_dataset,
    make_experiment,
)

pytestmark = pytest.mark.integration


def _service(
    session: AsyncSession,
    enqueued: Enqueued | None = None,
    llm: FakeLLM | None = None,
) -> ExperimentService:
    return ExperimentService(
        session, (llm or FakeLLM()).as_service(), enqueued or Enqueued()
    )


async def _set_status(
    factory: Factory, result_id: uuid.UUID, status: ResultStatus
) -> None:
    async with factory() as s:
        row = await s.get(ExperimentResult, result_id)
        assert row is not None
        row.status = status.value
        await s.commit()


async def _result_ids(factory: Factory, experiment_id: uuid.UUID):  # type: ignore[no-untyped-def]
    async with factory() as s:
        ids = await ExperimentResultRepository(s).list_ids_by_status(
            experiment_id, ResultStatus.PENDING
        )
    return ids


async def test_create_makes_pending_results_with_rendered_prompts(
    session_factory: Factory,
) -> None:
    owner = await seed_principal(session_factory)
    dataset_id = await make_dataset(session_factory, cases=3, owner=owner)
    enqueued = Enqueued()
    async with session_factory() as s:
        experiment = await _service(s, enqueued).create(
            owner, create_data(dataset_id, ("m1", "m2"))
        )
        await s.commit()
        assert experiment.owner_id == owner.user_id
        assert experiment.organization_id == owner.organization_id
        assert experiment.status == ExperimentStatus.PENDING
        assert experiment.config["targets"][0]["provider"] == "openai"
        rows = (
            await s.scalars(
                select(ExperimentResult).where(
                    ExperimentResult.experiment_id == experiment.id
                )
            )
        ).all()
    assert len(rows) == 6
    assert {r.status for r in rows} == {"pending"}
    assert {r.rendered_prompt for r in rows} == {"Q: in1", "Q: in2", "Q: in3"}
    assert enqueued.batches == []


async def test_case_set_frozen_after_dataset_grows(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=2)
    async with session_factory() as s:
        dataset_id = (await s.get(Experiment, experiment_id)).dataset_id  # type: ignore[union-attr]
        s.add(TestCase(dataset_id=dataset_id, position=3, input="new"))
        await s.commit()
    async with session_factory() as s:
        _, counts = await _service(s).get(ROOT, experiment_id)
    assert counts == {"pending": 2}


async def test_unknown_dataset(session_factory: Factory) -> None:
    owner = await seed_principal(session_factory)
    async with session_factory() as s:
        with pytest.raises(DatasetNotFoundError):
            await _service(s).create(owner, create_data(uuid.uuid4()))


async def test_empty_dataset_is_rejected(session_factory: Factory) -> None:
    owner = await seed_principal(session_factory)
    dataset_id = await make_dataset(session_factory, cases=0, owner=owner)
    async with session_factory() as s:
        with pytest.raises(ExperimentValidationError) as exc:
            await _service(s).create(owner, create_data(dataset_id))
    assert exc.value.field == "dataset_id"


async def test_unconfigured_provider_is_rejected(
    session_factory: Factory,
) -> None:
    owner = await seed_principal(session_factory)
    dataset_id = await make_dataset(session_factory, owner=owner)
    llm = FakeLLM(unconfigured={ProviderName.OPENAI})
    async with session_factory() as s:
        with pytest.raises(ExperimentValidationError) as exc:
            await _service(s, llm=llm).create(owner, create_data(dataset_id))
        assert exc.value.field == "config.targets"
        assert "OPENAI_API_KEY" in str(exc.value)
        await s.rollback()
    async with session_factory() as s:
        assert (await s.scalars(select(Experiment))).all() == []


async def test_result_cap(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("components.experiments.service.MAX_RESULTS", 5)
    owner = await seed_principal(session_factory)
    dataset_id = await make_dataset(session_factory, cases=3, owner=owner)
    async with session_factory() as s:
        with pytest.raises(ExperimentValidationError):
            await _service(s).create(owner, create_data(dataset_id, ("a", "b")))


async def test_start_enqueues_pending_and_sets_running(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=3)
    enqueued = Enqueued()
    async with session_factory() as s:
        count = await _service(s, enqueued).start(ROOT, experiment_id)
    assert count == 3
    assert len(enqueued.flat) == 3
    async with session_factory() as s:
        experiment, counts = await _service(s).get(ROOT, experiment_id)
    assert experiment.status == ExperimentStatus.RUNNING
    assert experiment.started_at is not None
    assert counts == {"pending": 3}


async def test_start_again_enqueues_only_pending(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, start=True)
    pending = await _result_ids(session_factory, experiment_id)
    await _set_status(session_factory, pending[0], ResultStatus.SUCCEEDED)
    await _set_status(session_factory, pending[1], ResultStatus.RUNNING)
    enqueued = Enqueued()
    async with session_factory() as s:
        count = await _service(s, enqueued).start(ROOT, experiment_id)
    assert count == 1
    assert enqueued.flat == [pending[2]]


@pytest.mark.parametrize(
    "status",
    ["completed", "partially_failed", "failed", "cancelled"],
)
async def test_start_final_experiment_conflicts(
    session_factory: Factory, status: str
) -> None:
    experiment_id = await make_experiment(session_factory)
    async with session_factory() as s:
        experiment = await s.get(Experiment, experiment_id)
        assert experiment is not None
        experiment.status = status
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(InvalidTransitionError):
            await _service(s).start(ROOT, experiment_id)


async def test_start_enqueue_failure_keeps_results_pending(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory)

    async def boom(ids):  # type: ignore[no-untyped-def]
        raise ConnectionError("broker down")

    async with session_factory() as s:
        service = ExperimentService(s, FakeLLM().as_service(), boom)
        with pytest.raises(ConnectionError):
            await service.start(ROOT, experiment_id)
    async with session_factory() as s:
        experiment, counts = await _service(s).get(ROOT, experiment_id)
        assert experiment.status == ExperimentStatus.RUNNING
        assert counts == {"pending": 3}
    enqueued = Enqueued()
    async with session_factory() as s:
        assert await _service(s, enqueued).start(ROOT, experiment_id) == 3


async def test_unknown_experiment(session_factory: Factory) -> None:
    missing = uuid.uuid4()
    async with session_factory() as s:
        service = _service(s)
        for call in (
            service.get(ROOT, missing),
            service.start(ROOT, missing),
            service.cancel(ROOT, missing),
            service.retry_failed(ROOT, missing),
            service.list_results(ROOT, missing, 10, 0),
        ):
            with pytest.raises(ExperimentNotFoundError):
                await call


async def test_cancel_pending_experiment(session_factory: Factory) -> None:
    experiment_id = await make_experiment(session_factory)
    async with session_factory() as s:
        experiment = await _service(s).cancel(ROOT, experiment_id)
        await s.commit()
        assert experiment.status == ExperimentStatus.CANCELLED
        assert experiment.finished_at is not None
    async with session_factory() as s:
        _, counts = await _service(s).get(ROOT, experiment_id)
    assert counts == {"cancelled": 3}


async def test_cancel_running_leaves_running_result(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, start=True)
    pending = await _result_ids(session_factory, experiment_id)
    await _set_status(session_factory, pending[0], ResultStatus.RUNNING)
    async with session_factory() as s:
        experiment = await _service(s).cancel(ROOT, experiment_id)
        await s.commit()
        assert experiment.status == ExperimentStatus.RUNNING
        assert experiment.cancel_requested is True
        _, counts = await _service(s).get(ROOT, experiment_id)
    assert counts == {"running": 1, "cancelled": 2}
    # Repeating while a result is still running is harmless.
    async with session_factory() as s:
        await _service(s).cancel(ROOT, experiment_id)


async def test_cancel_finished_conflicts(session_factory: Factory) -> None:
    experiment_id = await make_experiment(session_factory)
    async with session_factory() as s:
        experiment = await s.get(Experiment, experiment_id)
        assert experiment is not None
        experiment.status = "completed"
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(InvalidTransitionError):
            await _service(s).cancel(ROOT, experiment_id)


async def test_retry_failed_resets_only_failed(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, start=True)
    ids = await _result_ids(session_factory, experiment_id)
    async with session_factory() as s:
        ok = await s.get(ExperimentResult, ids[0])
        bad = await s.get(ExperimentResult, ids[1])
        other = await s.get(ExperimentResult, ids[2])
        experiment = await s.get(Experiment, experiment_id)
        assert ok and bad and other and experiment
        ok.status = "succeeded"
        ok.response_text = "kept"
        bad.status = "failed"
        bad.attempts = 3
        bad.error_kind = "timeout"
        other.status = "succeeded"
        experiment.status = "partially_failed"
        await s.commit()
    enqueued = Enqueued()
    async with session_factory() as s:
        assert (
            await _service(s, enqueued).retry_failed(ROOT, experiment_id) == 1
        )
    assert enqueued.flat == [ids[1]]
    async with session_factory() as s:
        experiment, counts = await _service(s).get(ROOT, experiment_id)
        assert experiment.status == ExperimentStatus.RUNNING
        assert experiment.finished_at is None
        assert counts == {"succeeded": 2, "pending": 1}
        bad = await s.get(ExperimentResult, ids[1])
        ok = await s.get(ExperimentResult, ids[0])
        assert bad and ok
        assert (bad.attempts, bad.error_kind) == (0, None)
        assert ok.response_text == "kept"


async def test_retry_completed_conflicts(session_factory: Factory) -> None:
    experiment_id = await make_experiment(session_factory)
    async with session_factory() as s:
        experiment = await s.get(Experiment, experiment_id)
        assert experiment is not None
        experiment.status = "completed"
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(InvalidTransitionError):
            await _service(s).retry_failed(ROOT, experiment_id)


async def test_list_results_filter_and_list_experiments(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=3)
    ids = await _result_ids(session_factory, experiment_id)
    await _set_status(session_factory, ids[0], ResultStatus.FAILED)
    async with session_factory() as s:
        service = _service(s)
        items, total = await service.list_results(
            ROOT, experiment_id, 10, 0, "failed"
        )
        assert (total, [i.id for i in items]) == (1, [ids[0]])
        experiments, total = await service.list_experiments(ROOT, 10, 0)
        assert total == 1 and experiments[0].id == experiment_id


async def test_start_again_enqueues_stale_running_results(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, start=True)
    ids = await _result_ids(session_factory, experiment_id)
    await _set_status(session_factory, ids[0], ResultStatus.RUNNING)
    await _set_status(session_factory, ids[1], ResultStatus.RUNNING)
    async with session_factory() as s:
        await s.execute(
            text(
                "update experiment_results set started_at = now() "
                "- interval '1 hour' where id = :id"
            ),
            {"id": ids[0]},
        )
        await s.execute(
            text(
                "update experiment_results set started_at = now() "
                "where id = :id"
            ),
            {"id": ids[1]},
        )
        await s.commit()
    enqueued = Enqueued()
    async with session_factory() as s:
        count = await _service(s, enqueued).start(ROOT, experiment_id)
    # The stale running result and the pending one; not the fresh running.
    assert count == 2
    assert sorted(enqueued.flat) == sorted([ids[0], ids[2]])


async def test_retry_failed_without_failed_results_finalizes(
    session_factory: Factory,
) -> None:
    experiment_id = await make_experiment(session_factory, cases=1)
    ids = await _result_ids(session_factory, experiment_id)
    await _set_status(session_factory, ids[0], ResultStatus.SUCCEEDED)
    async with session_factory() as s:
        experiment = await s.get(Experiment, experiment_id)
        assert experiment is not None
        experiment.status = "failed"
        await s.commit()
    enqueued = Enqueued()
    async with session_factory() as s:
        assert (
            await _service(s, enqueued).retry_failed(ROOT, experiment_id) == 0
        )
    assert enqueued.flat == []
    async with session_factory() as s:
        experiment, _ = await _service(s).get(ROOT, experiment_id)
        assert experiment.status == ExperimentStatus.COMPLETED
        assert experiment.finished_at is not None


async def test_superadmin_cannot_create(session_factory: Factory) -> None:
    dataset_id = await make_dataset(session_factory)
    async with session_factory() as s:
        with pytest.raises(OrganizationRequiredError):
            await _service(s).create(ROOT, create_data(dataset_id))
    async with session_factory() as s:
        assert (await s.scalars(select(Experiment))).all() == []


async def test_dataset_outside_scope_is_not_found(
    session_factory: Factory,
) -> None:
    owner = await seed_principal(session_factory)
    dataset_id = await make_dataset(session_factory, owner=owner)
    colleague = await seed_principal(
        session_factory, Role.STAFF, owner.organization_id
    )
    stranger = await seed_principal(session_factory, Role.ADMIN)
    for principal in (colleague, stranger):
        async with session_factory() as s:
            with pytest.raises(DatasetNotFoundError):
                await _service(s).create(principal, create_data(dataset_id))
    async with session_factory() as s:
        assert (await s.scalars(select(Experiment))).all() == []
        assert (await s.scalars(select(ExperimentResult))).all() == []


async def test_experiment_actions_use_the_scope(
    session_factory: Factory,
) -> None:
    owner = await seed_principal(session_factory)
    experiment_id = await make_experiment(session_factory, owner=owner)
    colleague = await seed_principal(
        session_factory, Role.STAFF, owner.organization_id
    )
    admin = await seed_principal(
        session_factory, Role.ADMIN, owner.organization_id
    )
    stranger = await seed_principal(session_factory, Role.ADMIN)
    enqueued = Enqueued()
    async with session_factory() as s:
        service = _service(s, enqueued)
        for denied in (colleague, stranger):
            for call in (
                service.get(denied, experiment_id),
                service.list_results(denied, experiment_id, 10, 0),
                service.start(denied, experiment_id),
                service.cancel(denied, experiment_id),
                service.retry_failed(denied, experiment_id),
            ):
                with pytest.raises(ExperimentNotFoundError):
                    await call
        assert enqueued.flat == []
        for allowed in (owner, admin, ROOT):
            experiment, _ = await service.get(allowed, experiment_id)
            assert experiment.id == experiment_id
        assert await service.start(admin, experiment_id) == 3
    assert len(enqueued.flat) == 3


async def test_list_filters_and_totals(session_factory: Factory) -> None:
    owner = await seed_principal(session_factory)
    other = await seed_principal(
        session_factory, Role.STAFF, owner.organization_id
    )
    mine = await make_experiment(session_factory, owner=owner)
    theirs = await make_experiment(session_factory, owner=other)
    async with session_factory() as s:
        service = _service(s)
        items, total = await service.list_experiments(owner, 10, 0)
        assert ([i.id for i in items], total) == ([mine], 1)
        items, total = await service.list_experiments(
            owner, 10, 0, owner_id=other.user_id
        )
        assert ([i.id for i in items], total) == ([], 0)
        items, total = await service.list_experiments(ROOT, 10, 0)
        assert {i.id for i in items} == {mine, theirs} and total == 2
        items, total = await service.list_experiments(
            ROOT, 10, 0, status="running"
        )
        assert total == 0
        items, total = await service.list_experiments(
            ROOT, 10, 0, status="pending", owner_id=other.user_id
        )
        assert ([i.id for i in items], total) == ([theirs], 1)
