from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.utils import get_settings_dependency
from components.core.config import SettingsSchema
from components.core.database import get_session
from conftest import TEST_JWT_SECRET
from tests.access import Actors, seed_actors


@pytest.fixture
async def actors(
    app: FastAPI,
    session_factory: Any,
) -> AsyncIterator[Actors]:
    """Users of every role against the test database, with the app wired to
    it. Needs a ``session_factory`` fixture whose database has the
    organizations, users and refresh tokens tables."""

    async def session_override() -> AsyncIterator[AsyncSession]:
        # Same unit-of-work semantics as get_session.
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings_dependency] = lambda: SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
        jwt_secret_key=TEST_JWT_SECRET,
    )
    result = await seed_actors(session_factory, app)
    yield result
    await result.aclose()
