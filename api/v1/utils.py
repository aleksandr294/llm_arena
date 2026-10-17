"""Shared API dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from components.auth.service import AccessTokenRejectedError, AuthService
from components.core.access import Principal
from components.core.config import SettingsSchema, get_settings
from components.core.database import get_session

# auto_error=False: a missing or malformed header must answer 401 with a
# Bearer challenge, not the default 403.
_bearer = HTTPBearer(auto_error=False)


def get_settings_dependency() -> SettingsSchema:
    """Overridable in tests."""
    return get_settings()


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[SettingsSchema, Depends(get_settings_dependency)],
) -> AuthService:
    return AuthService(session, settings)


def unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_principal(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Principal:
    """The caller, resolved from the database on every request so role
    changes and deactivation apply at once."""
    if credentials is None:
        raise unauthorized()
    try:
        return await service.authenticate(credentials.credentials)
    except AccessTokenRejectedError:
        # One generic answer; the reason is not disclosed to the caller.
        raise unauthorized() from None
