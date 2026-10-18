import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Table, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

if TYPE_CHECKING:
    # Type-only: the panel and admin helpers must not load at suite start
    # (fastadmin reads its environment at import time).
    from components.core.config import SettingsSchema
    from tests.access import Actors
    from tests.admin.helpers import World
    from tests.admin.panel_process import Panel

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


@pytest.fixture
def settings() -> "SettingsSchema":
    """Settings with placeholder URLs; nothing connects to them."""
    from components.core.config import SettingsSchema

    return SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
        jwt_secret_key=TEST_JWT_SECRET,
    )


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Engine on ``DATABASE_URL`` with the complete schema, fresh per test.

    Only the tables of the component models are created and dropped (never
    ``Base.metadata`` as a whole, which also holds tables that test modules
    register for themselves). Tests must never run against the main
    database.
    """
    import components.auth.models  # noqa: F401
    import components.datasets.models  # noqa: F401
    import components.evaluation.models  # noqa: F401
    import components.experiments.models  # noqa: F401
    import components.organizations.models  # noqa: F401
    import components.users.models  # noqa: F401
    from components.core.database import Base, create_engine

    tables = [
        cast(Table, mapper.local_table)
        for mapper in Base.registry.mappers
        if mapper.class_.__module__.startswith("components.")
    ]
    engine = create_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def api(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    settings: "SettingsSchema",
) -> None:
    """Point the app at the test database and settings."""
    from api.v1.utils import get_settings_dependency
    from components.core.database import get_session, session_scope

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_scope(session_factory) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings_dependency] = lambda: settings


@pytest.fixture
async def actors(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    api: None,
) -> AsyncIterator["Actors"]:
    """Users of every role against the test database, with the app wired to
    it (through ``api``)."""
    from tests.access import seed_actors

    result = await seed_actors(session_factory, app)
    yield result
    await result.aclose()


@pytest.fixture
async def world(
    session_factory: async_sessionmaker[AsyncSession],
) -> "World":
    """The seeded two-organization world (admin tests)."""
    from tests.admin.helpers import seed_world

    return await seed_world(session_factory)


@pytest.fixture
async def emails(
    session_factory: async_sessionmaker[AsyncSession], world: "World"
) -> dict[str, str]:
    """Sign-in emails by principal name; every user gets ``PASSWORD``."""
    from components.auth.security import hash_password
    from components.core.database import session_scope
    from components.users.models import User
    from tests.access import PASSWORD

    # ``session_scope`` commits on exit (the only place allowed to).
    async with session_scope(session_factory) as session:
        await session.execute(
            update(User).values(password_hash=await hash_password(PASSWORD))
        )
    async with session_factory() as session:
        rows = (await session.execute(select(User.id, User.email))).all()
    by_id = {row.id: row.email for row in rows}
    return {
        name: by_id[principal.user_id]
        for name, principal in world.principals.items()
    }


@pytest.fixture
def panel(emails: dict[str, str]) -> Iterator["Panel"]:
    """The panel mounted in a child process on the test database."""
    from tests.admin.panel_process import Panel

    running = Panel()
    yield running
    running.close()
