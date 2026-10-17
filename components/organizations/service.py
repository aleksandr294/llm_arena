"""Organization use cases. Owns the transaction of each operation."""

import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from components.auth.repository import RefreshTokenRepository
from components.organizations.models import Organization
from components.organizations.repository import OrganizationRepository


class OrganizationNotFoundError(Exception):
    def __init__(self) -> None:
        super().__init__("organization not found")


class DuplicateOrganizationNameError(Exception):
    def __init__(self) -> None:
        super().__init__("an organization with this name already exists")


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.organizations = OrganizationRepository(session)
        self.tokens = RefreshTokenRepository(session)

    async def create(self, name: str) -> Organization:
        if await self.organizations.get_by_name(name) is not None:
            raise DuplicateOrganizationNameError
        try:
            organization = await self.organizations.create(name)
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise DuplicateOrganizationNameError from None
        return organization

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
        organization = await self.get(organization_id)
        try:
            if name is not None and name != organization.name:
                existing = await self.organizations.get_by_name(name)
                if existing is not None and existing.id != organization.id:
                    raise DuplicateOrganizationNameError
                await self.organizations.rename(organization, name)
            if is_active is not None:
                organization.is_active = is_active
                if not is_active:
                    # Same transaction as the flag: no window in which the
                    # organization is inactive but its sessions survive.
                    await self.tokens.revoke_all_for_organization(
                        organization.id
                    )
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise DuplicateOrganizationNameError from None
        except DuplicateOrganizationNameError:
            await self.session.rollback()
            raise
        return organization
