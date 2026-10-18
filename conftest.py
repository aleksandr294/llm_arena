import os
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Test-only signing key; never used outside the test process.
TEST_JWT_SECRET = "test-only-jwt-signing-key-0123456789abcdef"
# Some modules read settings at import (collection) time, before fixtures.
os.environ.setdefault("JWT_SECRET_KEY", TEST_JWT_SECRET)


@pytest.fixture(autouse=True)
def _jwt_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``JWT_SECRET_KEY`` is a required setting, so every test that builds
    settings from the environment needs one."""
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)


@pytest.fixture
def app() -> FastAPI:
    from api.app import create_app
    from components.core.config import SettingsSchema

    # Explicit settings so unit tests need no DATABASE_URL in the environment;
    # the placeholders are never connected to.
    return create_app(
        SettingsSchema(
            database_url="postgresql+asyncpg://unused/unused",
            rabbitmq_url="amqp://unused/",
            jwt_secret_key=TEST_JWT_SECRET,
            admin_secret_key=None,
            _env_file=None,
        )
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client
