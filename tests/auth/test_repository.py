"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import Table, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.auth.models import RefreshToken
from components.auth.repository import RefreshTokenRepository
from components.auth.security import hash_token_id
from components.core.database import Base, create_engine
from components.core.enums import Role
from components.organizations.models import Organization
from components.organizations.repository import OrganizationRepository
from components.users.models import User
from components.users.repository import UserRepository

pytestmark = pytest.mark.integration

_MODELS = (Organization, User, RefreshToken)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_engine(os.environ["DATABASE_URL"])
    tables = [cast(Table, m.__table__) for m in _MODELS]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
        await s.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=tables)
    await engine.dispose()


async def _org(session: AsyncSession, name: str = "Acme") -> Organization:
    return await OrganizationRepository(session).create(name)


async def _user(
    session: AsyncSession,
    email: str = "ann@example.com",
    role: Role = Role.STAFF,
    organization_id: uuid.UUID | None = None,
) -> User:
    if organization_id is None and role is not Role.SUPERADMIN:
        organization_id = (await _org(session, f"org-{uuid.uuid4()}")).id
    return await UserRepository(session).create(
        email, "hash-value", role, organization_id
    )


async def _token(
    session: AsyncSession,
    user: User,
    family_id: uuid.UUID | None = None,
    jti: str | None = None,
) -> RefreshToken:
    return await RefreshTokenRepository(session).create(
        user.id,
        family_id or uuid.uuid4(),
        hash_token_id(jti or uuid.uuid4().hex),
        datetime.now(UTC) + timedelta(days=7),
    )


# --- organizations ---------------------------------------------------------


async def test_organization_defaults_and_lookup_ignores_case(
    session: AsyncSession,
) -> None:
    repo = OrganizationRepository(session)
    org = await repo.create("Acme Corp")
    await session.refresh(org)

    assert org.is_active is True
    assert org.created_at is not None
    found = await repo.get_by_name("ACME corp")
    assert found is not None and found.id == org.id
    assert await repo.get_by_name("other") is None


async def test_organization_name_unique_ignoring_case(
    session: AsyncSession,
) -> None:
    await _org(session, "Acme")

    with pytest.raises(IntegrityError):
        await _org(session, "aCME")


async def test_organization_rename_updates_lowercase_key(
    session: AsyncSession,
) -> None:
    repo = OrganizationRepository(session)
    org = await repo.create("Acme")

    await repo.rename(org, "Globex")

    assert (await repo.get_by_name("globex")) is not None
    assert (await repo.get_by_name("acme")) is None


async def test_organization_pagination(session: AsyncSession) -> None:
    repo = OrganizationRepository(session)
    for i in range(3):
        await repo.create(f"org{i}")

    page, total = await repo.list_page(limit=2, offset=0)
    rest, _ = await repo.list_page(limit=2, offset=2)

    assert total == 3
    assert len(page) == 2
    assert len(rest) == 1


# --- users -----------------------------------------------------------------


async def test_user_email_unique_ignoring_case(session: AsyncSession) -> None:
    org = await _org(session)
    await _user(session, "Ann@Example.com", organization_id=org.id)

    with pytest.raises(IntegrityError):
        await _user(session, "ann@EXAMPLE.com", organization_id=org.id)


async def test_user_lookup_by_email_ignores_case(session: AsyncSession) -> None:
    user = await _user(session, "Ann@Example.com")

    found = await UserRepository(session).get_by_email("ann@example.COM")

    assert found is not None and found.id == user.id
    assert found.email == "Ann@Example.com"
    assert await UserRepository(session).get_by_email("x@y.z") is None


async def test_user_defaults_and_role_value_is_lowercase(
    session: AsyncSession,
) -> None:
    user = await _user(session, role=Role.ADMIN)
    await session.refresh(user)

    assert user.is_active is True
    assert user.role == "admin"
    assert user.created_at is not None


async def test_superadmin_without_organization_is_allowed(
    session: AsyncSession,
) -> None:
    user = await _user(session, role=Role.SUPERADMIN)

    assert user.organization_id is None


@pytest.mark.parametrize("role", [Role.STAFF, Role.ADMIN])
async def test_check_rejects_non_superadmin_without_organization(
    session: AsyncSession, role: Role
) -> None:
    with pytest.raises(IntegrityError) as exc:
        await UserRepository(session).create("a@b.co", "h", role, None)

    assert "ck_users_role_organization" in str(exc.value)


async def test_check_rejects_superadmin_with_organization(
    session: AsyncSession,
) -> None:
    org = await _org(session)

    with pytest.raises(IntegrityError) as exc:
        await UserRepository(session).create(
            "a@b.co", "h", Role.SUPERADMIN, org.id
        )

    assert "ck_users_role_organization" in str(exc.value)


async def test_organization_with_users_cannot_be_deleted(
    session: AsyncSession,
) -> None:
    org = await _org(session)
    await _user(session, organization_id=org.id)

    with pytest.raises(IntegrityError):
        await OrganizationRepository(session).delete(org)


async def test_user_filters_and_pagination(session: AsyncSession) -> None:
    repo = UserRepository(session)
    org_a = await _org(session, "A")
    org_b = await _org(session, "B")
    await _user(session, "a1@x.co", Role.STAFF, org_a.id)
    await _user(session, "a2@x.co", Role.ADMIN, org_a.id)
    inactive = await _user(session, "b1@x.co", Role.STAFF, org_b.id)
    inactive.is_active = False
    await session.flush()

    in_a, total_a = await repo.list_page(10, 0, organization_id=org_a.id)
    admins, _ = await repo.list_page(10, 0, role=Role.ADMIN)
    inactives, _ = await repo.list_page(10, 0, is_active=False)
    page, total = await repo.list_page(2, 2)

    assert total_a == 2 and len(in_a) == 2
    assert [u.email for u in admins] == ["a2@x.co"]
    assert [u.id for u in inactives] == [inactive.id]
    assert total == 3 and len(page) == 1


# --- refresh tokens --------------------------------------------------------


async def test_token_lookup_by_hash(session: AsyncSession) -> None:
    user = await _user(session)
    stored = await _token(session, user, jti="abc")
    repo = RefreshTokenRepository(session)

    found = await repo.get_by_hash(hash_token_id("abc"))

    assert found is not None and found.id == stored.id
    assert found.used_at is None and found.revoked_at is None
    assert await repo.get_by_hash(hash_token_id("other")) is None
    assert len(found.token_hash) == 64


async def test_token_hash_is_unique(session: AsyncSession) -> None:
    user = await _user(session)
    await _token(session, user, jti="same")

    with pytest.raises(IntegrityError):
        await _token(session, user, jti="same")


async def test_mark_used_succeeds_once(session: AsyncSession) -> None:
    user = await _user(session)
    token = await _token(session, user)
    repo = RefreshTokenRepository(session)

    assert await repo.mark_used(token.id) is True
    assert await repo.mark_used(token.id) is False
    await session.refresh(token)
    assert token.used_at is not None


async def test_mark_used_fails_for_revoked_token(session: AsyncSession) -> None:
    user = await _user(session)
    token = await _token(session, user)
    repo = RefreshTokenRepository(session)
    await repo.revoke_family(token.family_id)

    assert await repo.mark_used(token.id) is False


async def test_revoke_family_only_touches_that_family(
    session: AsyncSession,
) -> None:
    user = await _user(session)
    family = uuid.uuid4()
    first = await _token(session, user, family)
    second = await _token(session, user, family)
    other = await _token(session, user)
    repo = RefreshTokenRepository(session)

    assert await repo.revoke_family(family) == 2
    assert await repo.revoke_family(family) == 0
    for token in (first, second, other):
        await session.refresh(token)
    assert first.revoked_at is not None and second.revoked_at is not None
    assert other.revoked_at is None


async def test_revoke_all_for_user(session: AsyncSession) -> None:
    ann = await _user(session, "ann@x.co")
    bob = await _user(session, "bob@x.co")
    await _token(session, ann)
    await _token(session, ann)
    bobs = await _token(session, bob)
    repo = RefreshTokenRepository(session)

    assert await repo.revoke_all_for_user(ann.id) == 2
    await session.refresh(bobs)
    assert bobs.revoked_at is None


async def test_deleting_user_cascades_to_tokens(session: AsyncSession) -> None:
    user = await _user(session)
    await _token(session, user)

    await UserRepository(session).delete(user)

    remaining = await session.scalars(select(RefreshToken))
    assert remaining.all() == []
