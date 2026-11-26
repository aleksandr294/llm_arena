"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch
from sqlalchemy import String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from components.core import database
from components.core.database import (
    Base,
    commit,
    create_engine,
    get_session,
    on_commit,
)
from components.core.repository import BaseRepository
from tests.helpers import checked_database_url

pytestmark = pytest.mark.integration


class ProbeModel(Base):
    __tablename__ = "persistence_probe"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class ProbeRepository(BaseRepository[ProbeModel]):
    model = ProbeModel


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(checked_database_url())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        return len((await session.scalars(select(ProbeModel))).all())


async def test_repository_crud(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        repo = ProbeRepository(session)
        entity = await repo.add(ProbeModel(name="a"))
        assert (await repo.get(entity.id)) is entity
        assert len((await session.scalars(select(ProbeModel))).all()) == 1

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

    engine = create_engine(checked_database_url())
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


async def test_every_failing_callback_is_reported(
    factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    ran: list[str] = []

    async def first() -> None:
        ran.append("first")
        raise ValueError("first-secret-text")

    async def second() -> None:
        ran.append("second")
        raise KeyError("second-secret-text")

    async def third() -> None:
        ran.append("third")

    async with factory() as session:
        await ProbeRepository(session).add(ProbeModel(name="kept"))
        for callback in (first, second, third):
            on_commit(session, callback)
        with (
            caplog.at_level(logging.ERROR, logger=database.logger.name),
            pytest.raises(ValueError, match="first-secret-text"),
        ):
            await commit(session)

    assert ran == ["first", "second", "third"]
    assert await _count(factory) == 1
    messages = [r for r in caplog.records if r.name == database.logger.name]
    assert [r.exception_type for r in messages] == ["KeyError"]  # type: ignore[attr-defined]
    assert "second-secret-text" not in caplog.text


async def test_failed_commit_discards_callbacks(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    ran: list[str] = []

    async def callback() -> None:
        ran.append("ran")

    async with factory() as session:
        on_commit(session, callback)
        session.add(ProbeModel(id=1, name="a"))
        session.add(ProbeModel(id=1, name="duplicate"))
        with pytest.raises(IntegrityError):
            await commit(session)
        await session.rollback()

        session.add(ProbeModel(id=2, name="b"))
        await commit(session)

    assert ran == []
