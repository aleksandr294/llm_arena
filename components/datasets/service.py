import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from components.core.access import Principal, require_organization
from components.datasets.models import Dataset, TestCase
from components.datasets.repository import (
    DatasetRepository,
    TestCaseRepository,
)
from components.datasets.schemas import (
    DatasetCreateSchema,
    TestCaseCreateSchema,
)


class DatasetNotFoundError(Exception):
    def __init__(self, dataset_id: uuid.UUID) -> None:
        super().__init__(f"Dataset {dataset_id} not found")
        self.dataset_id = dataset_id


class DatasetService:
    """Dataset business logic. Uses the caller's session and never commits;
    the request-scoped ``get_session`` owns the transaction. Every method
    takes the calling ``Principal``; datasets outside its scope are reported
    as not found. Content fields are deliberately never logged."""

    def __init__(self, session: AsyncSession) -> None:
        self.datasets = DatasetRepository(session)
        self.cases = TestCaseRepository(session)

    async def create_dataset(
        self, principal: Principal, data: DatasetCreateSchema
    ) -> Dataset:
        # Owner and organization always come from the caller, never from
        # the request body.
        dataset = Dataset(
            name=data.name,
            description=data.description,
            organization_id=require_organization(principal),
            owner_id=principal.user_id,
        )
        await self.datasets.add(dataset)
        # Load server-generated values so the object is readable after flush.
        await self.datasets.session.refresh(dataset)
        return dataset

    async def list_datasets(
        self,
        principal: Principal,
        limit: int,
        offset: int,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> tuple[Sequence[Dataset], int]:
        return await self.datasets.list_page(
            principal, limit, offset, created_from, created_to, owner_id
        )

    async def get_dataset(
        self, principal: Principal, dataset_id: uuid.UUID
    ) -> tuple[Dataset, int]:
        """Return the dataset and its number of test cases."""
        dataset = await self.datasets.get_scoped(dataset_id, principal)
        if dataset is None:
            raise DatasetNotFoundError(dataset_id)
        return dataset, await self.cases.count(dataset_id)

    async def add_case(
        self,
        principal: Principal,
        dataset_id: uuid.UUID,
        data: TestCaseCreateSchema,
    ) -> TestCase:
        # The row lock serializes concurrent adds so next_position is safe.
        if await self.datasets.get_for_update(dataset_id, principal) is None:
            raise DatasetNotFoundError(dataset_id)
        case = TestCase(
            dataset_id=dataset_id,
            position=await self.cases.next_position(dataset_id),
            input=data.input,
            expected_output=data.expected_output,
            metadata_=data.metadata,
        )
        await self.cases.add(case)
        await self.cases.session.refresh(case)
        return case

    async def list_cases(
        self,
        principal: Principal,
        dataset_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[TestCase], int]:
        if await self.datasets.get_scoped(dataset_id, principal) is None:
            raise DatasetNotFoundError(dataset_id)
        return await self.cases.list_page(dataset_id, limit, offset)
