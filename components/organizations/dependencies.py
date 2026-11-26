"""Wiring of ``OrganizationService``: the one place where it is built."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.organizations.service import (
    OrganizationService,
    RevokeSessions,
)


def build_organization_service(
    session: AsyncSession, revoke_sessions: RevokeSessions
) -> OrganizationService:
    return OrganizationService(session, revoke_sessions)
