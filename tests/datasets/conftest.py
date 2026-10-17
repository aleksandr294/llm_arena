import os
from collections.abc import AsyncIterator
from typing import cast

import pytest
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from components.core.database import Base, create_engine
from components.datasets.models import Dataset, TestCase
from tests.access import ACCESS_MODELS


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Engine on ``DATABASE_URL`` with fresh dataset tables per test."""
    engine = create_engine(os.environ["DATABASE_URL"])
    tables = [
        cast(Table, m.__table__) for m in (*ACCESS_MODELS, Dataset, TestCase)
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(engine, expire_on_commit=False)
