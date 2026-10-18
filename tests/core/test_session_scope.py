import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from components.core import database
from components.core.database import (
    commit,
    get_session,
    on_commit,
    session_scope,
)

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="needs DATABASE_URL"
)


@pytest.fixture(autouse=True)
async def _probe_table(engine: AsyncEngine) -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE session_scope_probe (n int)"))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS session_scope_probe"))


async def _add(session: AsyncSession, n: int) -> None:
    await session.execute(
        text("INSERT INTO session_scope_probe VALUES (:n)"), {"n": n}
    )


async def _count(session_factory: Any) -> int:
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT count(*) FROM session_scope_probe")
        )
        return int(result.scalar_one())


async def test_one_commit_per_operation(session_factory: Any) -> None:
    commits = 0

    def _count_commit(_: Any) -> None:
        nonlocal commits
        commits += 1

    async with session_scope(session_factory) as session:
        event.listen(session.sync_session, "after_commit", _count_commit)
        await _add(session, 1)
        await session.flush()
        await _add(session, 2)
    assert commits == 1
    assert await _count(session_factory) == 2


async def test_rollback_on_exception(session_factory: Any) -> None:
    with pytest.raises(RuntimeError):
        async with session_scope(session_factory) as session:
            await _add(session, 1)
            await session.flush()
            raise RuntimeError("boom")
    assert await _count(session_factory) == 0


async def test_session_closed_after_exit(session_factory: Any) -> None:
    async with session_scope(session_factory) as session:
        await _add(session, 1)
    assert not session.in_transaction()
    with pytest.raises(RuntimeError):
        async with session_scope(session_factory) as failing:
            await _add(failing, 2)
            raise RuntimeError("boom")
    assert not failing.in_transaction()


async def test_callbacks_run_after_commit_in_order(
    session_factory: Any,
) -> None:
    calls: list[str] = []

    async def first() -> None:
        calls.append("first")
        # The commit is already durable when callbacks run.
        assert await _count(session_factory) == 1

    async def second() -> None:
        calls.append("second")

    async with session_scope(session_factory) as session:
        await _add(session, 1)
        on_commit(session, first)
        on_commit(session, second)
        assert calls == []
    assert calls == ["first", "second"]


async def test_callbacks_not_run_on_rollback(session_factory: Any) -> None:
    calls: list[str] = []

    async def callback() -> None:
        calls.append("x")

    with pytest.raises(RuntimeError):
        async with session_scope(session_factory) as session:
            on_commit(session, callback)
            raise RuntimeError("boom")
    assert calls == []
    assert "on_commit_callbacks" not in session.info


async def test_callbacks_run_exactly_once(session_factory: Any) -> None:
    calls: list[str] = []

    async def callback() -> None:
        calls.append("x")

    async with session_scope(session_factory) as session:
        on_commit(session, callback)
        await commit(session)
        assert calls == ["x"]
        await commit(session)
    assert calls == ["x"]


async def test_failing_callback_keeps_commit_and_surfaces(
    session_factory: Any,
) -> None:
    calls: list[str] = []

    async def bad() -> None:
        calls.append("bad")
        raise ValueError("first")

    async def bad_too() -> None:
        calls.append("bad_too")
        raise KeyError("second")

    async def good() -> None:
        calls.append("good")

    with pytest.raises(ValueError, match="first"):
        async with session_scope(session_factory) as session:
            await _add(session, 1)
            on_commit(session, bad)
            on_commit(session, bad_too)
            on_commit(session, good)
    assert calls == ["bad", "bad_too", "good"]
    assert await _count(session_factory) == 1


async def test_get_session_commits_on_success(
    session_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        database, "get_session_factory", lambda: session_factory
    )
    generator = get_session()
    session = await anext(generator)
    await _add(session, 1)
    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    assert await _count(session_factory) == 1


async def test_get_session_rolls_back_on_error(
    session_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        database, "get_session_factory", lambda: session_factory
    )
    generator: AsyncIterator[AsyncSession] = get_session()
    session = await anext(generator)
    await _add(session, 1)
    await session.flush()
    with pytest.raises(RuntimeError):
        await generator.athrow(RuntimeError("boom"))  # type: ignore[attr-defined]
    assert await _count(session_factory) == 0
