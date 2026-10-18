"""Wiring of ``AuthService``: the one place where it is constructed."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.auth.service import AuthService
from components.core.config import SettingsSchema


def build_auth_service(
    session: AsyncSession, settings: SettingsSchema
) -> AuthService:
    return AuthService(session, settings)
