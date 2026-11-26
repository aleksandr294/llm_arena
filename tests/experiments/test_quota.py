"""Per-organization limit on running experiments. Need PostgreSQL."""

import asyncio
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from components.core.schemas import Principal
from components.experiments.dependencies import build_experiment_service
from components.experiments.enums import ExperimentStatus
from components.experiments.exceptions import ExperimentQuotaError
from components.experiments.models import Experiment
from components.experiments.service import ExperimentService
from tests.access import ROOT, seed_principal
from tests.experiments.helpers import (
    Enqueued,
    Factory,
    FakeLLM,
    make_experiment,
)


def _service(
    session: AsyncSession, limit: int, enqueued: Enqueued
) -> ExperimentService:
    return build_experiment_service(
        session, FakeLLM().as_service(), enqueued, limit
    )


async def _start(
    factory: Factory, experiment_id: uuid.UUID, limit: int, enqueued: Enqueued
) -> int:
    async with factory() as session:
        return await _service(session, limit, enqueued).start(
            ROOT, experiment_id
        )


async def _set_status(
    factory: Factory, experiment_id: uuid.UUID, status: ExperimentStatus
) -> None:
    async with factory() as session:
        await session.execute(
            update(Experiment)
            .where(Experiment.id == experiment_id)
            .values(status=status.value)
        )
        await session.commit()


async def _experiments(
    factory: Factory, owner: Principal, count: int
) -> list[uuid.UUID]:
    return [
        await make_experiment(factory, cases=1, owner=owner)
        for _ in range(count)
    ]


async def test_limit_reached_refuses_and_leaves_experiment_pending(
    session_factory: Factory,
) -> None:
    owner = await seed_principal(session_factory)
    first, second = await _experiments(session_factory, owner, 2)
    enqueued = Enqueued()
    await _start(session_factory, first, 1, enqueued)
    enqueued.batches.clear()

    with pytest.raises(ExperimentQuotaError):
        await _start(session_factory, second, 1, enqueued)

    assert enqueued.flat == []
    async with session_factory() as session:
        row = await session.get(Experiment, second)
        assert row is not None
        assert row.status == ExperimentStatus.PENDING.value


async def test_finished_experiments_do_not_count(
    session_factory: Factory,
) -> None:
    owner = await seed_principal(session_factory)
    first, second = await _experiments(session_factory, owner, 2)
    await _start(session_factory, first, 1, Enqueued())
    await _set_status(session_factory, first, ExperimentStatus.COMPLETED)

    assert await _start(session_factory, second, 1, Enqueued()) == 1


async def test_restarting_a_running_experiment_is_not_a_new_one(
    session_factory: Factory,
) -> None:
    owner = await seed_principal(session_factory)
    (only,) = await _experiments(session_factory, owner, 1)
    await _start(session_factory, only, 1, Enqueued())

    await _start(session_factory, only, 1, Enqueued())  # no quota error


async def test_other_organizations_are_not_counted(
    session_factory: Factory,
) -> None:
    mine = await seed_principal(session_factory)
    theirs = await seed_principal(session_factory)
    (a,) = await _experiments(session_factory, mine, 1)
    (b,) = await _experiments(session_factory, theirs, 1)
    await _start(session_factory, a, 1, Enqueued())

    await _start(session_factory, b, 1, Enqueued())


async def test_retry_failed_counts_as_a_start(
    session_factory: Factory,
) -> None:
    owner = await seed_principal(session_factory)
    running, failed = await _experiments(session_factory, owner, 2)
    await _start(session_factory, running, 1, Enqueued())
    await _set_status(session_factory, failed, ExperimentStatus.FAILED)

    async with session_factory() as session:
        with pytest.raises(ExperimentQuotaError):
            await _service(session, 1, Enqueued()).retry_failed(ROOT, failed)


async def test_concurrent_starts_cannot_overshoot(
    session_factory: Factory,
) -> None:
    owner = await seed_principal(session_factory)
    ids = await _experiments(session_factory, owner, 4)

    outcomes = await asyncio.gather(
        *[_start(session_factory, i, 2, Enqueued()) for i in ids],
        return_exceptions=True,
    )

    refused = [o for o in outcomes if isinstance(o, ExperimentQuotaError)]
    started = [o for o in outcomes if isinstance(o, int)]
    assert len(started) == 2 and len(refused) == 2
