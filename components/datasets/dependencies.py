"""Wiring of ``DatasetService``: the one place where it is constructed."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.datasets.service import DatasetService


def build_dataset_service(session: AsyncSession) -> DatasetService:
    return DatasetService(session)
