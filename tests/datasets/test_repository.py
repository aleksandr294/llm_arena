"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import os
import uuid
from collections.abc import AsyncIterator
from typing import cast

import pytest
from sqlalchemy import Table, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.database import Base, create_engine
from components.datasets.models import Dataset, TestCase
from components.datasets.repository import (
    DatasetRepository,
    TestCaseRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_engine(os.environ["DATABASE_URL"])
    tables = [cast(Table, m.__table__) for m in (Dataset, TestCase)]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
        await s.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
    await engine.dispose()


async def _dataset(session: AsyncSession, name: str = "d") -> Dataset:
    return await DatasetRepository(session).add(Dataset(name=name))


async def _case(
    session: AsyncSession, dataset: Dataset, position: int
) -> TestCase:
    return await TestCaseRepository(session).add(
        TestCase(dataset_id=dataset.id, position=position, input=f"i{position}")
    )


async def test_dataset_page_ordering_and_total(session: AsyncSession) -> None:
    repo = DatasetRepository(session)
    for i in range(5):
        await _dataset(session, f"d{i}")
        # created_at is the transaction start time; flush per row keeps ids
        # as the tie-breaker, so ordering must still be stable.
    first, total = await repo.list_page(limit=2, offset=0)
    second, _ = await repo.list_page(limit=2, offset=2)
    last, _ = await repo.list_page(limit=2, offset=4)
    assert total == 5
    assert [len(first), len(second), len(last)] == [2, 2, 1]
    keys = [(d.created_at, d.id) for d in [*first, *second, *last]]
    assert keys == sorted(keys)
    assert len({d.id for d in [*first, *second, *last]}) == 5
    empty, total = await repo.list_page(limit=2, offset=10)
    assert empty == [] and total == 5


async def test_case_page_ordered_by_position(session: AsyncSession) -> None:
    ds = await _dataset(session)
    other = await _dataset(session, "other")
    for pos in (3, 1, 2):
        await _case(session, ds, pos)
    await _case(session, other, 1)

    repo = TestCaseRepository(session)
    page, total = await repo.list_page(ds.id, limit=2, offset=0)
    rest, _ = await repo.list_page(ds.id, limit=2, offset=2)
    assert total == 3
    assert [c.position for c in page] == [1, 2]
    assert [c.position for c in rest] == [3]


async def test_next_position_and_count(session: AsyncSession) -> None:
    ds = await _dataset(session)
    repo = TestCaseRepository(session)
    assert await repo.next_position(ds.id) == 1
    assert await repo.count(ds.id) == 0
    await _case(session, ds, 1)
    await _case(session, ds, 2)
    assert await repo.next_position(ds.id) == 3
    assert await repo.count(ds.id) == 2
    assert await repo.next_position(uuid.uuid4()) == 1


async def test_get_for_update(session: AsyncSession) -> None:
    ds = await _dataset(session)
    repo = DatasetRepository(session)
    locked = await repo.get_for_update(ds.id)
    assert locked is not None and locked.id == ds.id
    assert await repo.get_for_update(uuid.uuid4()) is None


async def test_deleting_dataset_cascades_to_cases(
    session: AsyncSession,
) -> None:
    ds = await _dataset(session)
    await _case(session, ds, 1)
    await _case(session, ds, 2)
    ds_id = ds.id

    await session.execute(
        text("DELETE FROM datasets WHERE id = :i"), {"i": ds_id}
    )
    remaining = await session.scalar(
        text("SELECT count(*) FROM test_cases WHERE dataset_id = :i"),
        {"i": ds_id},
    )
    assert remaining == 0


async def test_duplicate_position_rejected(session: AsyncSession) -> None:
    ds = await _dataset(session)
    await _case(session, ds, 1)
    with pytest.raises(IntegrityError):
        await _case(session, ds, 1)
    await session.rollback()


async def test_same_position_allowed_in_other_dataset(
    session: AsyncSession,
) -> None:
    a = await _dataset(session, "a")
    b = await _dataset(session, "b")
    await _case(session, a, 1)
    await _case(session, b, 1)


async def test_case_roundtrip_with_null_fields_and_metadata(
    session: AsyncSession,
) -> None:
    ds = await _dataset(session)
    repo = TestCaseRepository(session)
    plain = await repo.add(TestCase(dataset_id=ds.id, position=1, input="x"))
    rich = await repo.add(
        TestCase(
            dataset_id=ds.id,
            position=2,
            input="q",
            expected_output="a",
            metadata_={"k": [1, "é"]},
        )
    )
    await session.refresh(plain)
    await session.refresh(rich)
    assert plain.expected_output is None and plain.metadata_ is None
    assert rich.metadata_ == {"k": [1, "é"]}
    assert rich.created_at is not None
