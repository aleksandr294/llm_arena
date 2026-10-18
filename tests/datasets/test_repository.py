"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import Table, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.database import Base, create_engine
from components.core.enums import Role
from components.core.schemas import Principal
from components.datasets.models import Dataset, TestCase
from components.datasets.repository import (
    DatasetRepository,
    TestCaseRepository,
)
from components.users.models import User
from tests.access import ACCESS_MODELS, seed_organization
from tests.helpers import checked_database_url

pytestmark = pytest.mark.integration


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_engine(checked_database_url())
    tables = [
        cast(Table, m.__table__) for m in (*ACCESS_MODELS, Dataset, TestCase)
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
        await s.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
    await engine.dispose()


async def _dataset(
    session: AsyncSession,
    name: str = "d",
    organization_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
) -> Dataset:
    return await DatasetRepository(session).add(
        Dataset(
            name=name,
            organization_id=organization_id or await seed_organization(session),
            owner_id=owner_id,
        )
    )


async def _user(
    session: AsyncSession, organization_id: uuid.UUID | None, role: Role
) -> Principal:
    user = User(
        email=f"{uuid.uuid4()}@example.com",
        email_lower=f"{uuid.uuid4()}@example.com",
        password_hash="x",
        role=role.value,
        organization_id=organization_id,
    )
    session.add(user)
    await session.flush()
    return Principal(user.id, organization_id, role)


SUPER = Principal(uuid.uuid4(), None, Role.SUPERADMIN)


async def _case(
    session: AsyncSession, dataset: Dataset, position: int
) -> TestCase:
    return await TestCaseRepository(session).add(
        TestCase(dataset_id=dataset.id, position=position, input=f"i{position}")
    )


async def test_dataset_page_ordering_and_total(session: AsyncSession) -> None:
    repo = DatasetRepository(session)
    org = await seed_organization(session)
    for i in range(5):
        await _dataset(session, f"d{i}", org)
        # created_at is the transaction start time; flush per row keeps ids
        # as the tie-breaker, so ordering must still be stable.
    first, total = await repo.list_page(SUPER, limit=2, offset=0)
    second, _ = await repo.list_page(SUPER, limit=2, offset=2)
    last, _ = await repo.list_page(SUPER, limit=2, offset=4)
    assert total == 5
    assert [len(first), len(second), len(last)] == [2, 2, 1]
    keys = [(d.created_at, d.id) for d in [*first, *second, *last]]
    assert keys == sorted(keys)
    assert len({d.id for d in [*first, *second, *last]}) == 5
    empty, total = await repo.list_page(SUPER, limit=2, offset=10)
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
    locked = await repo.get_for_update(ds.id, SUPER)
    assert locked is not None and locked.id == ds.id
    assert await repo.get_for_update(uuid.uuid4(), SUPER) is None


async def test_scoped_reads_by_role(session: AsyncSession) -> None:
    org = await seed_organization(session)
    other = await seed_organization(session)
    staff_a = await _user(session, org, Role.STAFF)
    staff_b = await _user(session, org, Role.STAFF)
    admin = await _user(session, org, Role.ADMIN)
    admin_other = await _user(session, other, Role.ADMIN)
    mine = await _dataset(session, "a", org, staff_a.user_id)
    theirs = await _dataset(session, "b", org, staff_b.user_id)
    legacy = await _dataset(session, "legacy", org, None)
    foreign = await _dataset(session, "f", other, admin_other.user_id)
    repo = DatasetRepository(session)

    async def visible(principal: Principal) -> set[uuid.UUID]:
        page, total = await repo.list_page(principal, 100, 0)
        assert total == len(page)
        return {d.id for d in page}

    assert await visible(staff_a) == {mine.id}
    assert await visible(admin) == {mine.id, theirs.id, legacy.id}
    assert await visible(admin_other) == {foreign.id}
    assert await visible(SUPER) == {mine.id, theirs.id, legacy.id, foreign.id}
    assert await repo.get_scoped(theirs.id, staff_a) is None
    assert await repo.get_scoped(legacy.id, staff_a) is None
    assert await repo.get_scoped(legacy.id, admin) is not None
    assert await repo.get_scoped(foreign.id, admin) is None
    assert await repo.get_for_update(theirs.id, staff_a) is None
    assert await repo.get_for_update(theirs.id, admin) is not None


async def test_owner_filter_cannot_widen_staff_scope(
    session: AsyncSession,
) -> None:
    org = await seed_organization(session)
    staff_a = await _user(session, org, Role.STAFF)
    staff_b = await _user(session, org, Role.STAFF)
    admin = await _user(session, org, Role.ADMIN)
    await _dataset(session, "a", org, staff_a.user_id)
    await _dataset(session, "b", org, staff_b.user_id)
    repo = DatasetRepository(session)
    page, total = await repo.list_page(staff_a, 10, 0, owner_id=staff_b.user_id)
    assert (list(page), total) == ([], 0)
    page, total = await repo.list_page(admin, 10, 0, owner_id=staff_b.user_id)
    assert total == 1 and page[0].owner_id == staff_b.user_id


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


async def test_created_range_inclusive_and_combined(
    session: AsyncSession,
) -> None:
    org = await seed_organization(session)
    repo = DatasetRepository(session)
    stamps = [datetime(2026, 1, d, tzinfo=UTC) for d in (1, 2, 3, 4)]
    for i, stamp in enumerate(stamps):
        dataset = await _dataset(session, f"d{i}", org)
        dataset.created_at = stamp
    await session.flush()

    async def names(**kw: Any) -> list[str]:
        page, total = await repo.list_page(SUPER, 10, 0, **kw)
        assert total == len(page)
        return [d.name for d in page]

    assert await names(created_from=stamps[1]) == ["d1", "d2", "d3"]
    assert await names(created_to=stamps[2]) == ["d0", "d1", "d2"]
    assert await names(created_from=stamps[1], created_to=stamps[2]) == [
        "d1",
        "d2",
    ]
    assert await names(created_from=stamps[1], created_to=stamps[1]) == ["d1"]


async def test_unscoped_base_methods_are_disabled(
    session: AsyncSession,
) -> None:
    from components.experiments.repository import ExperimentRepository

    ds = await _dataset(session)
    for repo in (DatasetRepository(session), ExperimentRepository(session)):
        with pytest.raises(NotImplementedError, match="scoped"):
            await repo.get(ds.id)
        with pytest.raises(NotImplementedError, match="scoped"):
            await repo.list()
        with pytest.raises(NotImplementedError, match="scoped"):
            await repo.delete(ds)  # type: ignore[arg-type]
