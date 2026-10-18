"""Plain helpers for the auth tests (not fixtures)."""

import uuid
from dataclasses import dataclass

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.auth.models import RefreshToken
from components.auth.security import hash_password
from components.core.enums import Role
from components.organizations.models import Organization
from components.organizations.repository import OrganizationRepository
from components.users.models import User
from components.users.repository import UserRepository

MODELS = (Organization, User, RefreshToken)
PASSWORD = "correct horse battery"

Factory = async_sessionmaker[AsyncSession]


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


async def login_headers(
    client: AsyncClient, user: Seeded, password: str = PASSWORD
) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
