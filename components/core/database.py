from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from components.core.config import get_settings


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


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Unit of work: commit on success, roll back on any error."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
