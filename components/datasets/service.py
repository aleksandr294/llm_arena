import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

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
    the request-scoped ``get_session`` owns the transaction. Content fields
    are deliberately never logged."""

    def __init__(self, session: AsyncSession) -> None:
        self.datasets = DatasetRepository(session)
        self.cases = TestCaseRepository(session)

    async def create_dataset(self, data: DatasetCreateSchema) -> Dataset:
        dataset = Dataset(name=data.name, description=data.description)
        await self.datasets.add(dataset)
        # Load server-generated values so the object is readable after flush.
        await self.datasets.session.refresh(dataset)
        return dataset

    async def list_datasets(
        self, limit: int, offset: int
    ) -> tuple[Sequence[Dataset], int]:
        return await self.datasets.list_page(limit, offset)

    async def get_dataset(self, dataset_id: uuid.UUID) -> tuple[Dataset, int]:
        """Return the dataset and its number of test cases."""
        dataset = await self.datasets.get(dataset_id)
        if dataset is None:
            raise DatasetNotFoundError(dataset_id)
        return dataset, await self.cases.count(dataset_id)

    async def add_case(
        self, dataset_id: uuid.UUID, data: TestCaseCreateSchema
    ) -> TestCase:
        # The row lock serializes concurrent adds so next_position is safe.
        if await self.datasets.get_for_update(dataset_id) is None:
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
        self, dataset_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[Sequence[TestCase], int]:
        if await self.datasets.get(dataset_id) is None:
            raise DatasetNotFoundError(dataset_id)
        return await self.cases.list_page(dataset_id, limit, offset)
