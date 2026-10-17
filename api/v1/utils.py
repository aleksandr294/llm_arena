"""Shared API dependencies."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
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


@dataclass(frozen=True, slots=True)
class CreatedRangeSchema:
    """Validated inclusive creation-time bounds of a listing."""

    created_from: datetime | None
    created_to: datetime | None


def _invalid_query(name: str, message: str) -> HTTPException:
    # Same shape as FastAPI validation errors.
    return HTTPException(
        422,  # constant name differs across Starlette versions
        [{"loc": ["query", name], "msg": message, "type": "value_error"}],
    )


def get_created_range(
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
) -> CreatedRangeSchema:
    """Timezone-aware ISO 8601 bounds; naive values and an inverted range
    answer 422. In a URL, encode ``+`` of an offset as ``%2B``."""
    for name, value in (
        ("created_from", created_from),
        ("created_to", created_to),
    ):
        if value is not None and value.utcoffset() is None:
            raise _invalid_query(name, "must include a timezone offset")
    if created_from and created_to and created_from > created_to:
        raise _invalid_query("created_from", "must not be after created_to")
    return CreatedRangeSchema(created_from, created_to)
