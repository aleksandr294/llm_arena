import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from components.core.repository import BaseRepository
from components.datasets.models import Dataset, TestCase


class DatasetRepository(BaseRepository[Dataset]):
    model = Dataset

    async def list_page(
        self, limit: int, offset: int
    ) -> tuple[Sequence[Dataset], int]:
        """Return one page ordered by ``(created_at, id)`` and the total."""
        total = await self.session.scalar(
            select(func.count()).select_from(Dataset)
        )
        result = await self.session.scalars(
            select(Dataset)
            .order_by(Dataset.created_at, Dataset.id)
            .limit(limit)
            .offset(offset)
        )
        return result.all(), total or 0

    async def get_for_update(self, dataset_id: uuid.UUID) -> Dataset | None:
        """Lock the dataset row to serialize concurrent case creation."""
        return await self.session.scalar(
            select(Dataset)
            .where(Dataset.id == dataset_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )


class TestCaseRepository(BaseRepository[TestCase]):
    model = TestCase
    __test__ = False  # not a pytest test class

    async def list_page(
        self, dataset_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[Sequence[TestCase], int]:
        """Return one page of a dataset's cases by position and the total."""
        total = await self.count(dataset_id)
        result = await self.session.scalars(
            select(TestCase)
            .where(TestCase.dataset_id == dataset_id)
            .order_by(TestCase.position)
            .limit(limit)
            .offset(offset)
        )
        return result.all(), total

    async def count(self, dataset_id: uuid.UUID) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(TestCase)
            .where(TestCase.dataset_id == dataset_id)
        )
        return total or 0

    async def next_position(self, dataset_id: uuid.UUID) -> int:
        """Return ``max(position) + 1``, or 1 for an empty dataset. Callers
        must hold the dataset row lock to avoid racing other writers."""
        current = await self.session.scalar(
            select(func.max(TestCase.position)).where(
                TestCase.dataset_id == dataset_id
            )
        )
        return (current or 0) + 1
