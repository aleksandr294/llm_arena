import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.v1.docs import errors
from api.v1.utils import (
    CreatedRangeSchema,
    SessionDep,
    get_created_range,
    get_principal,
)
from components.core.exceptions import OrganizationRequiredError
from components.core.schemas import Principal
from components.datasets.dependencies import build_dataset_service
from components.datasets.exceptions import DatasetNotFoundError
from components.datasets.schemas import (
    DatasetCreateSchema,
    DatasetDetailSchema,
    DatasetPageSchema,
    DatasetSchema,
    TestCaseCreateSchema,
    TestCasePageSchema,
    TestCaseSchema,
)
from components.datasets.service import DatasetService

router = APIRouter(prefix="/datasets", tags=["datasets"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def get_dataset_service(session: SessionDep) -> DatasetService:
    return build_dataset_service(session)


ServiceDep = Annotated[DatasetService, Depends(get_dataset_service)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DatasetSchema,
    summary="Create a dataset",
    responses=errors(401, 403),
)
async def create_dataset(
    body: DatasetCreateSchema, service: ServiceDep, principal: PrincipalDep
) -> DatasetSchema:
    try:
        dataset = await service.create_dataset(principal, body)
    except OrganizationRequiredError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    return DatasetSchema.model_validate(dataset)


@router.get(
    "",
    response_model=DatasetPageSchema,
    summary="List datasets",
    responses=errors(401),
)
async def list_datasets(
    service: ServiceDep,
    principal: PrincipalDep,
    created: Annotated[CreatedRangeSchema, Depends(get_created_range)],
    limit: Limit = 50,
    offset: Offset = 0,
    owner_id: uuid.UUID | None = None,
) -> DatasetPageSchema:
    items, total = await service.list_datasets(
        principal,
        limit,
        offset,
        created.created_from,
        created.created_to,
        owner_id,
    )
    return DatasetPageSchema(
        items=[DatasetSchema.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{dataset_id}",
    response_model=DatasetDetailSchema,
    summary="Get a dataset",
    responses=errors(401, 404),
)
async def get_dataset(
    dataset_id: uuid.UUID, service: ServiceDep, principal: PrincipalDep
) -> DatasetDetailSchema:
    try:
        dataset, case_count = await service.get_dataset(principal, dataset_id)
    except DatasetNotFoundError:
        raise _not_found() from None
    return DatasetDetailSchema(
        **DatasetSchema.model_validate(dataset).model_dump(),
        case_count=case_count,
    )


@router.post(
    "/{dataset_id}/cases",
    status_code=status.HTTP_201_CREATED,
    response_model=TestCaseSchema,
    summary="Add a test case",
    responses=errors(401, 404),
)
async def add_case(
    dataset_id: uuid.UUID,
    body: TestCaseCreateSchema,
    service: ServiceDep,
    principal: PrincipalDep,
) -> TestCaseSchema:
    try:
        case = await service.add_case(principal, dataset_id, body)
    except DatasetNotFoundError:
        raise _not_found() from None
    return TestCaseSchema.model_validate(case)


@router.get(
    "/{dataset_id}/cases",
    response_model=TestCasePageSchema,
    summary="List test cases",
    responses=errors(401, 404),
)
async def list_cases(
    dataset_id: uuid.UUID,
    service: ServiceDep,
    principal: PrincipalDep,
    limit: Limit = 50,
    offset: Offset = 0,
) -> TestCasePageSchema:
    try:
        items, total = await service.list_cases(
            principal, dataset_id, limit, offset
        )
    except DatasetNotFoundError:
        raise _not_found() from None
    return TestCasePageSchema(
        items=[TestCaseSchema.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )
