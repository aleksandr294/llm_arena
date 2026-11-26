import logging
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
)
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from components.core.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Shared declarative base; component models extend it so Alembic sees
    their tables through ``Base.metadata``."""


def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_pre_ping=True,
        # Statement errors would otherwise embed bound values (filter text,
        # emails, ids) in exception messages that end up in logs.
        hide_parameters=True,
    )


@lru_cache
def get_engine() -> AsyncEngine:
    return create_engine(get_settings().database_url.get_secret_value())


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def dispose_engine() -> None:
    """Close the pooled connections of the engine, if one was ever built
    (never builds one, which would also read the settings)."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()


_CALLBACKS_KEY = "on_commit_callbacks"

CommitCallback = Callable[[], Awaitable[None]]


def on_commit(session: AsyncSession, callback: CommitCallback) -> None:
    """Run ``callback`` once, after the session's next successful commit.

    Callbacks are kept in ``session.info`` and awaited by ``commit`` instead
    of using the SQLAlchemy ``after_commit`` event: that event fires
    synchronously inside the sync session, where an async callback cannot be
    awaited reliably. Whoever commits must therefore use ``commit`` (or
    ``session_scope``), never ``session.commit()`` directly.
    """
    session.info.setdefault(_CALLBACKS_KEY, []).append(callback)


async def commit(session: AsyncSession) -> None:
    """Commit, then run the ``on_commit`` callbacks in registration order.

    Callbacks run after the commit, so a failing one never undoes it. Every
    callback is attempted; the first exception is raised afterwards and the
    others are logged by exception class only (their messages may carry
    content). If the commit itself fails the callbacks are discarded, so they
    cannot run after a later successful commit. Each callback runs at most
    once, even if ``commit`` is called again.
    """
    try:
        await session.commit()
    except BaseException:
        session.info.pop(_CALLBACKS_KEY, None)
        raise
    callbacks: list[CommitCallback] = session.info.pop(_CALLBACKS_KEY, [])
    first_error: Exception | None = None
    for callback in callbacks:
        try:
            await callback()
        except Exception as exc:
            if first_error is None:
                first_error = exc
            else:
                logger.error(
                    "on_commit callback failed",
                    extra={"exception_type": type(exc).__name__},
                )
    if first_error is not None:
        raise first_error


# What the flows that open several short sessions of their own (the runner,
# export streaming) receive from their entry point.
SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Unit of work: commit once on success, roll back on any error, always
    close. ``factory`` is for tests; it defaults to the cached factory."""
    if factory is None:
        factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await commit(session)
        except BaseException:
            # Pending callbacks belong to work that is not durable.
            session.info.pop(_CALLBACKS_KEY, None)
            await session.rollback()
            raise


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one ``session_scope`` per request."""
    async with session_scope() as session:
        yield session
