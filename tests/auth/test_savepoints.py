"""A lost unique-key race undoes only the failed insert, never the other
changes of the caller's transaction. Need PostgreSQL (``DATABASE_URL``)."""

import uuid
from typing import Any

import pytest

from components.auth.dependencies import build_auth_service
from components.core.config import SettingsSchema
from components.core.enums import Role
from components.core.schemas import Principal
from components.organizations.dependencies import build_organization_service
from components.organizations.exceptions import DuplicateOrganizationNameError
from components.organizations.models import Organization
from components.organizations.repository import OrganizationRepository
from components.users.dependencies import build_user_service
from components.users.exceptions import DuplicateEmailError
from components.users.models import User
from components.users.repository import UserRepository
from tests.auth.helpers import PASSWORD, Factory, seed_user


async def _none(*_: Any) -> None:
    """Stands in for the existence check that a concurrent insert beats."""
    return None


async def test_user_conflict_keeps_an_unrelated_pending_change(
    session_factory: Factory, settings: SettingsSchema
) -> None:
    admin = await seed_user(session_factory, "boss@example.com", Role.ADMIN)
    await seed_user(
        session_factory,
        "taken@example.com",
        existing_organization_id=admin.organization_id,
    )
    caller = Principal(admin.user_id, admin.organization_id, Role.ADMIN)
    async with session_factory() as session:
        organization = await session.get(Organization, admin.organization_id)
        assert organization is not None
        organization.name = "renamed before the conflict"  # pending
        service = build_user_service(
            session, build_auth_service(session, settings)
        )
        service.users.get_by_email = _none  # type: ignore[method-assign,assignment]

        with pytest.raises(DuplicateEmailError):
            await service.create_user(
                caller, "Taken@Example.com", PASSWORD, Role.STAFF, None
            )

        await session.commit()
    async with session_factory() as session:
        stored = await session.get(Organization, admin.organization_id)
        assert stored is not None
        assert stored.name == "renamed before the conflict"
        users = await UserRepository(session).get_by_email("taken@example.com")
        assert users is not None


async def test_superadmin_conflict_keeps_an_unrelated_pending_change(
    session_factory: Factory, settings: SettingsSchema
) -> None:
    await seed_user(session_factory, "root@example.com", Role.SUPERADMIN)
    async with session_factory() as session:
        extra = Organization(name="pending org", name_lower="pending org")
        session.add(extra)
        service = build_user_service(
            session, build_auth_service(session, settings)
        )
        service.users.get_by_email = _none  # type: ignore[method-assign,assignment]

        with pytest.raises(DuplicateEmailError):
            await service.create_superadmin("ROOT@example.com", PASSWORD)

        await session.commit()
    async with session_factory() as session:
        assert (
            await OrganizationRepository(session).get_by_name("pending org")
            is not None
        )


async def test_organization_create_conflict_keeps_pending_change(
    session_factory: Factory, settings: SettingsSchema
) -> None:
    admin = await seed_user(session_factory, "boss@example.com", Role.ADMIN)
    async with session_factory() as session:
        await OrganizationRepository(session).create("Existing Name")
        await session.commit()
    async with session_factory() as session:
        other = await session.get(User, admin.user_id)
        assert other is not None
        other.email = "boss-renamed@example.com"  # pending, unrelated
        service = _organizations(session, settings)
        service.organizations.get_by_name = _none

        with pytest.raises(DuplicateOrganizationNameError):
            await service.create("existing name")

        await session.commit()
    async with session_factory() as session:
        stored = await session.get(User, admin.user_id)
        assert stored is not None
        assert stored.email == "boss-renamed@example.com"


async def test_organization_rename_conflict_keeps_pending_change(
    session_factory: Factory, settings: SettingsSchema
) -> None:
    admin = await seed_user(session_factory, "boss@example.com", Role.ADMIN)
    async with session_factory() as session:
        await OrganizationRepository(session).create("Taken")
        await session.commit()
    async with session_factory() as session:
        other = await session.get(User, admin.user_id)
        assert other is not None
        other.email = "boss-renamed@example.com"
        service = _organizations(session, settings)
        real = service.organizations.get_by_name
        calls = 0

        async def lookup(name: str) -> Organization | None:
            nonlocal calls
            calls += 1  # the duplicate check misses, the insert collides
            return None if calls == 1 else await real(name)

        service.organizations.get_by_name = lookup

        assert admin.organization_id is not None
        with pytest.raises(DuplicateOrganizationNameError):
            await service.update(admin.organization_id, name="taken")

        await session.commit()
    async with session_factory() as session:
        stored = await session.get(User, admin.user_id)
        assert stored is not None
        assert stored.email == "boss-renamed@example.com"


async def test_organization_deactivation_revokes_sessions_through_the_port(
    session_factory: Factory,
) -> None:
    revoked: list[uuid.UUID] = []

    async def revoke(organization_id: uuid.UUID) -> int:
        revoked.append(organization_id)
        return 0

    admin = await seed_user(session_factory, "boss@example.com", Role.ADMIN)
    assert admin.organization_id is not None
    async with session_factory() as session:
        service = build_organization_service(session, revoke)
        organization = await service.update(
            admin.organization_id, is_active=False
        )
        assert organization.is_active is False
    assert revoked == [admin.organization_id]


def _organizations(session: Any, settings: SettingsSchema) -> Any:
    auth = build_auth_service(session, settings)
    return build_organization_service(
        session, auth.revoke_organization_sessions
    )
