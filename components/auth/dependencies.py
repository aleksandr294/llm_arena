"""Wiring of ``AuthService``: the one place where it is constructed."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.auth.service import AuthService
from components.auth.throttle import AuthThrottleService
from components.core.config import SettingsSchema


def build_auth_throttle_service(
    session: AsyncSession, settings: SettingsSchema
) -> AuthThrottleService:
    return AuthThrottleService(session, settings)


def build_auth_service(
    session: AsyncSession, settings: SettingsSchema
) -> AuthService:
    return AuthService(
        session, settings, build_auth_throttle_service(session, settings)
    )
