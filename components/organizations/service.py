"""Organization use cases. Uses the caller's session and never commits."""

import uuid
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from components.organizations.exceptions import (
    DuplicateOrganizationNameError,
    OrganizationNotFoundError,
)
from components.organizations.models import Organization
from components.organizations.repository import OrganizationRepository

# Port: ends the sessions of every user of an organization (given its id)
# inside the caller's transaction and returns how many tokens it revoked.
RevokeSessions = Callable[[uuid.UUID], Awaitable[int]]


class OrganizationService:
    def __init__(
        self, session: AsyncSession, revoke_sessions: RevokeSessions
    ) -> None:
        self.session = session
        self.revoke_sessions = revoke_sessions
        self.organizations = OrganizationRepository(session)

    async def create(self, name: str) -> Organization:
        if await self.organizations.get_by_name(name) is not None:
            raise DuplicateOrganizationNameError
        try:
            # SAVEPOINT: a lost unique-name race undoes only this insert,
            # not the caller's other pending changes.
            async with self.session.begin_nested():
                return await self.organizations.create(name)
        except IntegrityError:
            raise DuplicateOrganizationNameError from None

    async def list(
        self, limit: int, offset: int
    ) -> tuple[Sequence[Organization], int]:
        return await self.organizations.list_page(limit, offset)

    async def get(self, organization_id: uuid.UUID) -> Organization:
        organization = await self.organizations.get(organization_id)
        if organization is None:
            raise OrganizationNotFoundError
        return organization

    async def update(
        self,
        organization_id: uuid.UUID,
        name: str | None = None,
        is_active: bool | None = None,
    ) -> Organization:
        organization = await self.organizations.get_for_update(organization_id)
        if organization is None:
            raise OrganizationNotFoundError
        if name is not None and name != organization.name:
            existing = await self.organizations.get_by_name(name)
            if existing is not None and existing.id != organization.id:
                raise DuplicateOrganizationNameError
            try:
                async with self.session.begin_nested():
                    await self.organizations.rename(organization, name)
            except IntegrityError:
                raise DuplicateOrganizationNameError from None
        if is_active is not None:
            organization.is_active = is_active
            if not is_active:
                # Same transaction as the flag: no window in which the
                # organization is inactive but its sessions survive.
                await self.revoke_sessions(organization.id)
        return organization
