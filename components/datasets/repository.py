import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from components.core.access import Principal, scope_clause
from components.core.repository import BaseRepository
from components.datasets.models import Dataset, TestCase

_USE_SCOPED = "use the scoped method"


class DatasetRepository(BaseRepository[Dataset]):
    """Methods take a ``Principal`` and treat out-of-scope rows as missing.
    The inherited unscoped ``get``/``delete``/``list`` are disabled so a
    request path cannot bypass ownership by accident."""

    model = Dataset

    async def get(self, entity_id: Any) -> Dataset | None:
        raise NotImplementedError(_USE_SCOPED)

    async def delete(self, entity: Dataset) -> None:
        raise NotImplementedError(_USE_SCOPED)

    async def list(
        self, limit: int = 100, offset: int = 0
    ) -> Sequence[Dataset]:
        raise NotImplementedError(_USE_SCOPED)

    async def list_page(
        self,
        principal: Principal,
        limit: int,
        offset: int,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> tuple[Sequence[Dataset], int]:
        """Return one visible page ordered by ``(created_at, id)`` and the
        total of visible datasets matching the filters (bounds inclusive).
        ``owner_id`` only narrows the scope, so it cannot widen it."""
        conditions = [scope_clause(Dataset, principal)]
        if created_from is not None:
            conditions.append(Dataset.created_at >= created_from)
        if created_to is not None:
            conditions.append(Dataset.created_at <= created_to)
        if owner_id is not None:
            conditions.append(Dataset.owner_id == owner_id)
        total = await self.session.scalar(
            select(func.count()).select_from(Dataset).where(*conditions)
        )
        result = await self.session.scalars(
            select(Dataset)
            .where(*conditions)
            .order_by(Dataset.created_at, Dataset.id)
            .limit(limit)
            .offset(offset)
        )
        return result.all(), total or 0

    async def get_scoped(
        self, dataset_id: uuid.UUID, principal: Principal
    ) -> Dataset | None:
        return await self.session.scalar(
            select(Dataset).where(
                Dataset.id == dataset_id, scope_clause(Dataset, principal)
            )
        )

    async def get_for_update(
        self, dataset_id: uuid.UUID, principal: Principal
    ) -> Dataset | None:
        """Lock a visible dataset row to serialize concurrent case
        creation."""
        return await self.session.scalar(
            select(Dataset)
            .where(Dataset.id == dataset_id, scope_clause(Dataset, principal))
            .with_for_update()
            .execution_options(populate_existing=True)
        )


class TestCaseRepository(BaseRepository[TestCase]):
    model = TestCase
    __test__ = False  # not a pytest test class
    # Cases carry no owner: they inherit the access of their dataset, so
    # callers fetch the dataset with ``DatasetRepository`` scoping first.

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
