"""Read-only panel views of datasets and their test cases."""

from typing import Any, ClassVar

from fastadmin import register
from fastadmin.models.schemas import WidgetType
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.core import ReadOnlyScopedAdmin
from components.datasets.models import Dataset, TestCase
from components.datasets.repository import TestCaseRepository
from components.users.models import User


@register(Dataset)
class DatasetAdmin(ReadOnlyScopedAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    ordering = ("-created_at",)
    computed_fields: ClassVar[dict[str, WidgetType]] = {
        "owner_email": WidgetType.Input,
        "test_case_count": WidgetType.InputNumber,
    }

    async def computed_values(
        self, session: AsyncSession, obj: Any
    ) -> dict[str, Any]:
        owner = None
        if obj.owner_id is not None:
            owner = await session.scalar(
                select(User.email).where(User.id == obj.owner_id)
            )
        return {
            "owner_email": owner,
            "test_case_count": await TestCaseRepository(session).count(obj.id),
        }


@register(TestCase)
class TestCaseAdmin(ReadOnlyScopedAdmin):
    # ``input``, ``expected_output`` and the metadata are untrusted and can be
    # large: detail view only.
    list_display = ("position", "created_at")
    # fastadmin reads columns by key, and the key "metadata" resolves to the
    # declarative base's MetaData instead of the column (see ``metadata_``),
    # which cannot be serialized.
    exclude = ("metadata",)
    ordering = ("-created_at",)
    computed_fields: ClassVar[dict[str, WidgetType]] = {
        "dataset_name": WidgetType.Input
    }

    async def computed_values(
        self, session: AsyncSession, obj: Any
    ) -> dict[str, Any]:
        name = await session.scalar(
            select(Dataset.name).where(Dataset.id == obj.dataset_id)
        )
        return {"dataset_name": name}
