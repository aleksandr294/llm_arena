"""Wiring of ``DatasetService``: the one place where it is constructed."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.core.config import SettingsSchema
from components.datasets.service import DatasetService


def build_dataset_service(
    session: AsyncSession, settings: SettingsSchema
) -> DatasetService:
    return DatasetService(session, settings.max_cases_per_dataset)
