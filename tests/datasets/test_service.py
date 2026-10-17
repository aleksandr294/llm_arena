"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from components.core.access import OrganizationRequiredError, Principal
from components.datasets.schemas import (
    DatasetCreateSchema,
    TestCaseCreateSchema,
)
from components.datasets.service import DatasetNotFoundError, DatasetService
from tests.access import Actors

pytestmark = pytest.mark.integration


async def _new_dataset(
    factory: async_sessionmaker,  # type: ignore[type-arg]
    principal: Principal,
) -> uuid.UUID:
    async with factory() as session:
        dataset = await DatasetService(session).create_dataset(
            principal, DatasetCreateSchema(name="d")
        )
        await session.commit()
        return dataset.id


async def test_positions_start_at_one_and_increment(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    actors: Actors,
) -> None:
    dataset_id = await _new_dataset(
        session_factory, actors.principal("staff_a")
    )
    async with session_factory() as session:
        service = DatasetService(session)
        me = actors.principal("staff_a")
        first = await service.add_case(
            me, dataset_id, TestCaseCreateSchema(input="a")
        )
        second = await service.add_case(
            me, dataset_id, TestCaseCreateSchema(input="b")
        )
        assert (first.position, second.position) == (1, 2)
        assert first.expected_output is None
        assert first.metadata_ is None


async def test_unknown_dataset(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    actors: Actors,
) -> None:
    missing = uuid.uuid4()
    async with session_factory() as session:
        service = DatasetService(session)
        me = actors.principal("staff_a")
        with pytest.raises(DatasetNotFoundError):
            await service.get_dataset(me, missing)
        with pytest.raises(DatasetNotFoundError):
            await service.add_case(me, missing, TestCaseCreateSchema(input="a"))
        with pytest.raises(DatasetNotFoundError):
            await service.list_cases(me, missing, 10, 0)


async def test_get_dataset_counts_cases(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    actors: Actors,
) -> None:
    dataset_id = await _new_dataset(
        session_factory, actors.principal("staff_a")
    )
    async with session_factory() as session:
        service = DatasetService(session)
        me = actors.principal("staff_a")
        for text in ("a", "b"):
            await service.add_case(
                me, dataset_id, TestCaseCreateSchema(input=text)
            )
        dataset, count = await service.get_dataset(me, dataset_id)
        assert (dataset.id, count) == (dataset_id, 2)


async def test_content_round_trip(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    actors: Actors,
) -> None:
    dataset_id = await _new_dataset(
        session_factory, actors.principal("staff_a")
    )
    text = 'He said "hi"\nline 2\t\\ é中文 \U0001f600 '
    meta = {"k": text, "n": [1, {"x": None}]}
    async with session_factory() as session:
        service = DatasetService(session)
        me = actors.principal("staff_a")
        await service.add_case(
            me,
            dataset_id,
            TestCaseCreateSchema(
                input=text, expected_output=text, metadata=meta
            ),
        )
        await session.commit()
    async with session_factory() as session:
        cases, total = await DatasetService(session).list_cases(
            actors.principal("staff_a"), dataset_id, 10, 0
        )
    assert total == 1
    assert cases[0].input == text
    assert cases[0].expected_output == text
    assert cases[0].metadata_ == meta


async def test_concurrent_adds_get_unique_gap_free_positions(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    actors: Actors,
) -> None:
    dataset_id = await _new_dataset(
        session_factory, actors.principal("staff_a")
    )
    n = 20

    async def add(i: int) -> int:
        async with session_factory() as session:
            case = await DatasetService(session).add_case(
                actors.principal("staff_a"),
                dataset_id,
                TestCaseCreateSchema(input=f"c{i}"),
            )
            await session.commit()
            return case.position

    positions = await asyncio.gather(*(add(i) for i in range(n)))

    assert sorted(positions) == list(range(1, n + 1))
    async with session_factory() as session:
        cases, total = await DatasetService(session).list_cases(
            actors.principal("staff_a"), dataset_id, 100, 0
        )
    assert total == n
    assert [c.position for c in cases] == list(range(1, n + 1))


async def test_create_records_owner_and_organization(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    actors: Actors,
) -> None:
    async with session_factory() as session:
        dataset = await DatasetService(session).create_dataset(
            actors.principal("staff_a"), DatasetCreateSchema(name="d")
        )
    assert dataset.owner_id == actors["staff_a"].user_id
    assert dataset.organization_id == actors.org_id
    async with session_factory() as session:
        admin_made = await DatasetService(session).create_dataset(
            actors.principal("admin_other"), DatasetCreateSchema(name="d")
        )
    assert admin_made.owner_id == actors["admin_other"].user_id
    assert admin_made.organization_id == actors.other_org_id


async def test_superadmin_cannot_create(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    actors: Actors,
) -> None:
    async with session_factory() as session:
        with pytest.raises(OrganizationRequiredError):
            await DatasetService(session).create_dataset(
                actors.principal("superadmin"), DatasetCreateSchema(name="d")
            )


async def test_out_of_scope_equals_missing(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    actors: Actors,
) -> None:
    dataset_id = await _new_dataset(
        session_factory, actors.principal("staff_a")
    )
    async with session_factory() as session:
        service = DatasetService(session)
        for name in ("staff_b", "admin_other"):
            who = actors.principal(name)
            with pytest.raises(DatasetNotFoundError):
                await service.get_dataset(who, dataset_id)
            with pytest.raises(DatasetNotFoundError):
                await service.add_case(
                    who, dataset_id, TestCaseCreateSchema(input="a")
                )
            with pytest.raises(DatasetNotFoundError):
                await service.list_cases(who, dataset_id, 10, 0)
            assert (await service.list_datasets(who, 10, 0))[1] == 0
        for name in ("staff_a", "admin", "superadmin"):
            dataset, _ = await service.get_dataset(
                actors.principal(name), dataset_id
            )
            assert dataset.id == dataset_id
