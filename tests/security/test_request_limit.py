"""Integration test: needs PostgreSQL (``DATABASE_URL``)."""

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.datasets.models import Dataset
from conftest import TEST_JWT_SECRET
from tests.access import Actors

pytestmark = pytest.mark.integration

LIMIT = 400


@pytest.fixture
def app() -> FastAPI:
    """Shadows the root ``app`` fixture with a small body limit."""
    from api.app import create_app
    from components.core.config import SettingsSchema

    return create_app(
        SettingsSchema(
            database_url="postgresql+asyncpg://unused/unused",
            rabbitmq_url="amqp://unused/",
            jwt_secret_key=TEST_JWT_SECRET,
            admin_secret_key=None,
            max_request_body_bytes=LIMIT,
            _env_file=None,
        )
    )


async def _dataset_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        result = await session.scalar(select(func.count()).select_from(Dataset))
    return int(result or 0)


async def test_over_limit_dataset_body_is_413_and_stores_nothing(
    actors: Actors,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = await actors.client("staff_a")
    too_big = await client.post(
        "/api/v1/datasets", json={"name": "d", "description": "x" * LIMIT}
    )
    assert too_big.status_code == 413
    assert too_big.json() == {"detail": "Request body too large"}
    assert await _dataset_count(session_factory) == 0

    accepted = await client.post("/api/v1/datasets", json={"name": "ok"})
    assert accepted.status_code == 201
    assert await _dataset_count(session_factory) == 1
