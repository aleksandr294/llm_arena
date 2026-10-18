"""Wiring of ``UserService``: the one place where it is constructed."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.auth.service import AuthService
from components.users.service import UserService


def build_user_service(
    session: AsyncSession, auth_service: AuthService
) -> UserService:
    return UserService(session, auth_service)
