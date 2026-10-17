import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from components.core.repository import BaseRepository
from components.organizations.models import Organization


class OrganizationRepository(BaseRepository[Organization]):
    model = Organization

    async def create(self, name: str, is_active: bool = True) -> Organization:
        return await self.add(
            Organization(
                name=name, name_lower=name.lower(), is_active=is_active
            )
        )

    async def get_by_name(self, name: str) -> Organization | None:
        """Look up by name ignoring case."""
        return await self.session.scalar(
            select(Organization).where(Organization.name_lower == name.lower())
        )

    async def rename(self, organization: Organization, name: str) -> None:
        organization.name = name
        organization.name_lower = name.lower()
        await self.session.flush()

    async def list_page(
        self, limit: int, offset: int
    ) -> tuple[Sequence[Organization], int]:
        """Return one page ordered by ``(created_at, id)`` and the total."""
        total = await self.session.scalar(
            select(func.count()).select_from(Organization)
        )
        result = await self.session.scalars(
            select(Organization)
            .order_by(Organization.created_at, Organization.id)
            .limit(limit)
            .offset(offset)
        )
        return result.all(), total or 0

    async def exists(self, organization_id: uuid.UUID) -> bool:
        return (await self.get(organization_id)) is not None
