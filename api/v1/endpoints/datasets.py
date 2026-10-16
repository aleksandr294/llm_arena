import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from components.core.database import get_session
from components.datasets.schemas import (
    DatasetCreateSchema,
    DatasetDetailSchema,
    DatasetPageSchema,
    DatasetSchema,
    TestCaseCreateSchema,
    TestCasePageSchema,
    TestCaseSchema,
)
from components.datasets.service import DatasetNotFoundError, DatasetService

router = APIRouter(prefix="/datasets", tags=["datasets"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def get_dataset_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DatasetService:
    return DatasetService(session)


ServiceDep = Annotated[DatasetService, Depends(get_dataset_service)]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=DatasetSchema
)
async def create_dataset(
    body: DatasetCreateSchema, service: ServiceDep
) -> DatasetSchema:
    return DatasetSchema.model_validate(await service.create_dataset(body))


@router.get("", response_model=DatasetPageSchema)
async def list_datasets(
    service: ServiceDep, limit: Limit = 50, offset: Offset = 0
) -> DatasetPageSchema:
    items, total = await service.list_datasets(limit, offset)
    return DatasetPageSchema(
        items=[DatasetSchema.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{dataset_id}", response_model=DatasetDetailSchema)
async def get_dataset(
    dataset_id: uuid.UUID, service: ServiceDep
) -> DatasetDetailSchema:
    try:
        dataset, case_count = await service.get_dataset(dataset_id)
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
)
async def add_case(
    dataset_id: uuid.UUID, body: TestCaseCreateSchema, service: ServiceDep
) -> TestCaseSchema:
    try:
        case = await service.add_case(dataset_id, body)
    except DatasetNotFoundError:
        raise _not_found() from None
    return TestCaseSchema.model_validate(case)


@router.get("/{dataset_id}/cases", response_model=TestCasePageSchema)
async def list_cases(
    dataset_id: uuid.UUID,
    service: ServiceDep,
    limit: Limit = 50,
    offset: Offset = 0,
) -> TestCasePageSchema:
    try:
        items, total = await service.list_cases(dataset_id, limit, offset)
    except DatasetNotFoundError:
        raise _not_found() from None
    return TestCasePageSchema(
        items=[TestCaseSchema.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )
