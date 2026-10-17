"""Shared access-control test helpers: organizations, users of every role,
principals and authenticated clients. Needs the organizations, users and
refresh tokens tables (plus whatever the test itself uses)."""

import uuid
from dataclasses import dataclass, field

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.auth.models import RefreshToken
from components.auth.security import hash_password
from components.core.access import Principal, Role
from components.organizations.models import Organization
from components.organizations.repository import OrganizationRepository
from components.users.models import User
from components.users.repository import UserRepository

# Tables a test database needs for users, tokens and ownership foreign keys.
ACCESS_MODELS = (Organization, User, RefreshToken)
PASSWORD = "correct horse battery"

Factory = async_sessionmaker[AsyncSession]

# A caller that sees everything, for service-level tests that act on data
# someone else created. It has no organization, so it cannot create data.
ROOT = Principal(uuid.uuid4(), None, Role.SUPERADMIN)


async def seed_principal(
    factory: Factory,
    role: Role = Role.STAFF,
    organization_id: uuid.UUID | None = None,
) -> Principal:
    """Commit a user (in a new organization unless given) with a dummy
    password hash and return its principal; it cannot log in."""
    async with factory() as session:
        if organization_id is None and role is not Role.SUPERADMIN:
            organization_id = await seed_organization(session)
        user = await UserRepository(session).create(
            f"u-{uuid.uuid4()}@example.com", "unused", role, organization_id
        )
        await session.commit()
        return Principal(user.id, organization_id, role)


async def seed_organization(session: AsyncSession) -> uuid.UUID:
    """Insert an organization (flushed, not committed) and return its id."""
    org = await OrganizationRepository(session).create(f"org-{uuid.uuid4()}")
    return org.id


@dataclass(frozen=True)
class Actor:
    name: str
    user_id: uuid.UUID
    organization_id: uuid.UUID | None
    role: Role
    email: str

    @property
    def principal(self) -> Principal:
        return Principal(self.user_id, self.organization_id, self.role)


@dataclass
class Actors:
    """One organization with staff A, staff B and an admin, a second
    organization with an admin, and a superadmin. Logging in is lazy
    because password hashing is slow."""

    app: FastAPI
    org_id: uuid.UUID
    other_org_id: uuid.UUID
    users: dict[str, Actor]
    _clients: list[AsyncClient] = field(default_factory=list)
    _headers: dict[str, dict[str, str]] = field(default_factory=dict)

    def __getitem__(self, name: str) -> Actor:
        return self.users[name]

    def principal(self, name: str) -> Principal:
        return self.users[name].principal

    async def headers(self, name: str) -> dict[str, str]:
        if name not in self._headers:
            async with AsyncClient(
                transport=ASGITransport(app=self.app), base_url="http://test"
            ) as login:
                response = await login.post(
                    "/api/v1/auth/login",
                    json={
                        "email": self.users[name].email,
                        "password": PASSWORD,
                    },
                )
            assert response.status_code == 200
            token = response.json()["access_token"]
            self._headers[name] = {"Authorization": f"Bearer {token}"}
        return self._headers[name]

    async def client(self, name: str) -> AsyncClient:
        """A client that sends ``name``'s access token on every request."""
        client = AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://test",
            headers=await self.headers(name),
        )
        self._clients.append(client)
        return client

    async def aclose(self) -> None:
        for client in self._clients:
            await client.aclose()


async def seed_actors(factory: Factory, app: FastAPI) -> Actors:
    password_hash = await hash_password(PASSWORD)
    async with factory() as session:
        orgs = OrganizationRepository(session)
        org = await orgs.create(f"org-{uuid.uuid4()}")
        other = await orgs.create(f"org-{uuid.uuid4()}")
        spec = [
            ("staff_a", Role.STAFF, org.id),
            ("staff_b", Role.STAFF, org.id),
            ("admin", Role.ADMIN, org.id),
            ("admin_other", Role.ADMIN, other.id),
            ("superadmin", Role.SUPERADMIN, None),
        ]
        users: dict[str, Actor] = {}
        for name, role, org_id in spec:
            email = f"{name}@example.com"
            user = await UserRepository(session).create(
                email, password_hash, role, org_id
            )
            users[name] = Actor(name, user.id, org_id, role, email)
        await session.commit()
        return Actors(app, org.id, other.id, users)
