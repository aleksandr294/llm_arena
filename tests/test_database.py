"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from components.core import database
from components.core.database import Base, create_engine, get_session
from components.core.repository import BaseRepository

pytestmark = pytest.mark.integration


class ProbeModel(Base):
    __tablename__ = "persistence_probe"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class ProbeRepository(BaseRepository[ProbeModel]):
    model = ProbeModel


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        return len(await ProbeRepository(session).list())


async def test_repository_crud(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        repo = ProbeRepository(session)
        entity = await repo.add(ProbeModel(name="a"))
        assert (await repo.get(entity.id)) is entity
        assert len(await repo.list()) == 1

        entity.name = "b"
        await session.flush()
        await session.refresh(entity)
        assert entity.name == "b"

        await repo.delete(entity)
        assert await repo.get(entity.id) is None
        await session.rollback()


async def test_unit_of_work_commits_on_success(
    factory: async_sessionmaker[AsyncSession], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(database, "get_session_factory", lambda: factory)

    gen = get_session()
    session = await anext(gen)
    await ProbeRepository(session).add(ProbeModel(name="kept"))
    with pytest.raises(StopAsyncIteration):
        await anext(gen)

    assert await _count(factory) == 1


async def test_unit_of_work_rolls_back_on_error(
    factory: async_sessionmaker[AsyncSession], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(database, "get_session_factory", lambda: factory)

    gen = get_session()
    session = await anext(gen)
    await ProbeRepository(session).add(ProbeModel(name="lost"))
    with pytest.raises(RuntimeError):
        await gen.athrow(RuntimeError("boom"))

    assert await _count(factory) == 0


async def test_autogenerate_detects_model_table(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = create_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn))

        def diff(sync_conn: Any) -> list[Any]:
            ctx = MigrationContext.configure(sync_conn)
            return list(compare_metadata(ctx, Base.metadata))

        ops = await conn.run_sync(diff)
    await engine.dispose()

    assert any(
        op[0] == "add_table" and op[1].name == "persistence_probe" for op in ops
    )
