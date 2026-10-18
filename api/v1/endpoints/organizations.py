import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.docs import errors
from api.v1.utils import get_principal
from components.core.database import get_session
from components.core.enums import Role
from components.core.schemas import Principal
from components.organizations.schemas import (
    OrganizationCreateSchema,
    OrganizationPageSchema,
    OrganizationSchema,
    OrganizationUpdateSchema,
)
from components.organizations.service import (
    DuplicateOrganizationNameError,
    OrganizationNotFoundError,
    OrganizationService,
)

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


async def require_superadmin(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    if principal.role is not Role.SUPERADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    return principal


# Authentication (401) runs before the role check (403).
router = APIRouter(
    prefix="/organizations",
    tags=["organizations"],
    dependencies=[Depends(require_superadmin)],
)


def get_organization_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrganizationService:
    return OrganizationService(session)


ServiceDep = Annotated[OrganizationService, Depends(get_organization_service)]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")


def _conflict() -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, "An organization with this name exists"
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=OrganizationSchema,
    summary="Create an organization",
    description="Superadmin only.",
    responses=errors(401, 403, 409),
)
async def create_organization(
    body: OrganizationCreateSchema, service: ServiceDep
) -> OrganizationSchema:
    try:
        organization = await service.create(body.name)
    except DuplicateOrganizationNameError:
        raise _conflict() from None
    return OrganizationSchema.model_validate(organization)


@router.get(
    "",
    response_model=OrganizationPageSchema,
    summary="List organizations",
    description="Superadmin only.",
    responses=errors(401, 403),
)
async def list_organizations(
    service: ServiceDep, limit: Limit = 50, offset: Offset = 0
) -> OrganizationPageSchema:
    items, total = await service.list(limit, offset)
    return OrganizationPageSchema(
        items=[OrganizationSchema.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{organization_id}",
    response_model=OrganizationSchema,
    summary="Get an organization",
    description="Superadmin only.",
    responses=errors(401, 403, 404),
)
async def get_organization(
    organization_id: uuid.UUID, service: ServiceDep
) -> OrganizationSchema:
    try:
        return OrganizationSchema.model_validate(
            await service.get(organization_id)
        )
    except OrganizationNotFoundError:
        raise _not_found() from None


@router.patch(
    "/{organization_id}",
    response_model=OrganizationSchema,
    summary="Update an organization",
    description=(
        "Superadmin only. Blocking an organization revokes its sessions."
    ),
    responses=errors(401, 403, 404, 409),
)
async def update_organization(
    organization_id: uuid.UUID,
    body: OrganizationUpdateSchema,
    service: ServiceDep,
) -> OrganizationSchema:
    try:
        organization = await service.update(
            organization_id, body.name, body.is_active
        )
    except OrganizationNotFoundError:
        raise _not_found() from None
    except DuplicateOrganizationNameError:
        raise _conflict() from None
    return OrganizationSchema.model_validate(organization)
