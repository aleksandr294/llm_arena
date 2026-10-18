"""Wiring of ``OrganizationService``: the one place where it is built."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.organizations.service import OrganizationService


def build_organization_service(session: AsyncSession) -> OrganizationService:
    return OrganizationService(session)
