import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import cast

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from api.v1.utils import get_settings_dependency
from components.auth.models import RefreshToken
from components.auth.security import hash_password
from components.core.config import SettingsSchema
from components.core.database import (
    Base,
    create_engine,
    get_session,
    session_scope,
)
from components.core.enums import Role
from components.organizations.models import Organization
from components.organizations.repository import OrganizationRepository
from components.users.models import User
from components.users.repository import UserRepository
from conftest import TEST_JWT_SECRET

MODELS = (Organization, User, RefreshToken)
PASSWORD = "correct horse battery"

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
def settings() -> SettingsSchema:
    return SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
        jwt_secret_key=TEST_JWT_SECRET,
    )


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(os.environ["DATABASE_URL"])
    tables = [cast(Table, m.__table__) for m in MODELS]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> Factory:
    return async_sessionmaker(engine, expire_on_commit=False)


@dataclass(frozen=True)
class Seeded:
    user_id: uuid.UUID
    organization_id: uuid.UUID | None
    email: str


async def seed_user(
    factory: Factory,
    email: str = "Ann@Example.com",
    role: Role = Role.STAFF,
    is_active: bool = True,
    org_active: bool = True,
    password: str = PASSWORD,
    existing_organization_id: uuid.UUID | None = None,
) -> Seeded:
    async with factory() as session:
        organization_id = existing_organization_id
        if organization_id is None and role is not Role.SUPERADMIN:
            org = await OrganizationRepository(session).create(
                f"org-{uuid.uuid4()}", is_active=org_active
            )
            organization_id = org.id
        user = await UserRepository(session).create(
            email,
            await hash_password(password),
            role,
            organization_id,
            is_active=is_active,
        )
        await session.commit()
        return Seeded(user.id, organization_id, email)


@pytest.fixture
def api(
    app: FastAPI, session_factory: Factory, settings: SettingsSchema
) -> None:
    """Point the app at the test database and settings."""

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_scope(session_factory) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings_dependency] = lambda: settings


async def login_headers(
    client: AsyncClient, user: Seeded, password: str = PASSWORD
) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
