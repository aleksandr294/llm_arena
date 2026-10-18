import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.docs import errors
from api.v1.utils import get_principal, get_settings_dependency
from components.core.config import SettingsSchema
from components.core.database import get_session
from components.core.enums import Role
from components.core.schemas import Principal
from components.users.exceptions import (
    DuplicateEmailError,
    UserConflictError,
    UserForbiddenError,
    UserInputError,
    UserNotFoundError,
)
from components.users.schemas import (
    UserCreateSchema,
    UserPageSchema,
    UserSchema,
    UserUpdateSchema,
)
from components.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def get_user_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserService:
    return UserService(session)


ServiceDep = Annotated[UserService, Depends(get_user_service)]
SettingsDep = Annotated[SettingsSchema, Depends(get_settings_dependency)]


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "User not found")


def _invalid(exc: UserInputError) -> RequestValidationError:
    """Same shape as FastAPI's own 422 so clients see the field name."""
    return RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body", exc.field),
                "msg": str(exc),
                "input": None,
            }
        ]
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=UserSchema,
    summary="Create a user",
    responses=errors(401, 403, 409),
)
async def create_user(
    body: UserCreateSchema, principal: PrincipalDep, service: ServiceDep
) -> UserSchema:
    try:
        user = await service.create_user(
            principal,
            body.email,
            body.password,
            body.role,
            body.organization_id,
        )
    except UserForbiddenError:
        raise _forbidden() from None
    except UserInputError as exc:
        raise _invalid(exc) from None
    except DuplicateEmailError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A user with this email exists"
        ) from None
    return UserSchema.model_validate(user)


@router.get(
    "",
    response_model=UserPageSchema,
    summary="List users",
    responses=errors(401),
)
async def list_users(
    principal: PrincipalDep,
    service: ServiceDep,
    limit: Limit = 50,
    offset: Offset = 0,
    organization_id: uuid.UUID | None = None,
    role: Role | None = None,
    is_active: bool | None = None,
) -> UserPageSchema:
    items, total = await service.list_users(
        principal, limit, offset, organization_id, role, is_active
    )
    return UserPageSchema(
        items=[UserSchema.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{user_id}",
    response_model=UserSchema,
    summary="Get a user",
    responses=errors(401, 404),
)
async def get_user(
    user_id: uuid.UUID, principal: PrincipalDep, service: ServiceDep
) -> UserSchema:
    try:
        return UserSchema.model_validate(
            await service.get_user(principal, user_id)
        )
    except UserNotFoundError:
        raise _not_found() from None


@router.patch(
    "/{user_id}",
    response_model=UserSchema,
    summary="Update a user",
    responses=errors(401, 403, 404, 409),
)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdateSchema,
    principal: PrincipalDep,
    service: ServiceDep,
    settings: SettingsDep,
) -> UserSchema:
    try:
        user = await service.update_user(
            principal,
            user_id,
            settings,
            body.role,
            body.is_active,
            body.password,
        )
    except UserForbiddenError:
        raise _forbidden() from None
    except UserNotFoundError:
        raise _not_found() from None
    except UserConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except UserInputError as exc:
        raise _invalid(exc) from None
    return UserSchema.model_validate(user)
