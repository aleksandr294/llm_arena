"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from components.datasets.schemas import (
    DatasetCreateSchema,
    TestCaseCreateSchema,
)
from components.datasets.service import DatasetNotFoundError, DatasetService

pytestmark = pytest.mark.integration


async def _new_dataset(factory: async_sessionmaker) -> uuid.UUID:  # type: ignore[type-arg]
    async with factory() as session:
        dataset = await DatasetService(session).create_dataset(
            DatasetCreateSchema(name="d")
        )
        await session.commit()
        return dataset.id


async def test_positions_start_at_one_and_increment(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    dataset_id = await _new_dataset(session_factory)
    async with session_factory() as session:
        service = DatasetService(session)
        first = await service.add_case(
            dataset_id, TestCaseCreateSchema(input="a")
        )
        second = await service.add_case(
            dataset_id, TestCaseCreateSchema(input="b")
        )
        assert (first.position, second.position) == (1, 2)
        assert first.expected_output is None
        assert first.metadata_ is None


async def test_unknown_dataset(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    missing = uuid.uuid4()
    async with session_factory() as session:
        service = DatasetService(session)
        with pytest.raises(DatasetNotFoundError):
            await service.get_dataset(missing)
        with pytest.raises(DatasetNotFoundError):
            await service.add_case(missing, TestCaseCreateSchema(input="a"))
        with pytest.raises(DatasetNotFoundError):
            await service.list_cases(missing, 10, 0)


async def test_get_dataset_counts_cases(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    dataset_id = await _new_dataset(session_factory)
    async with session_factory() as session:
        service = DatasetService(session)
        for text in ("a", "b"):
            await service.add_case(dataset_id, TestCaseCreateSchema(input=text))
        dataset, count = await service.get_dataset(dataset_id)
        assert (dataset.id, count) == (dataset_id, 2)


async def test_content_round_trip(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    dataset_id = await _new_dataset(session_factory)
    text = 'He said "hi"\nline 2\t\\ é中文 \U0001f600 '
    meta = {"k": text, "n": [1, {"x": None}]}
    async with session_factory() as session:
        service = DatasetService(session)
        await service.add_case(
            dataset_id,
            TestCaseCreateSchema(
                input=text, expected_output=text, metadata=meta
            ),
        )
        await session.commit()
    async with session_factory() as session:
        cases, total = await DatasetService(session).list_cases(
            dataset_id, 10, 0
        )
    assert total == 1
    assert cases[0].input == text
    assert cases[0].expected_output == text
    assert cases[0].metadata_ == meta


async def test_concurrent_adds_get_unique_gap_free_positions(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    dataset_id = await _new_dataset(session_factory)
    n = 20

    async def add(i: int) -> int:
        async with session_factory() as session:
            case = await DatasetService(session).add_case(
                dataset_id, TestCaseCreateSchema(input=f"c{i}")
            )
            await session.commit()
            return case.position

    positions = await asyncio.gather(*(add(i) for i in range(n)))

    assert sorted(positions) == list(range(1, n + 1))
    async with session_factory() as session:
        cases, total = await DatasetService(session).list_cases(
            dataset_id, 100, 0
        )
    assert total == n
    assert [c.position for c in cases] == list(range(1, n + 1))
