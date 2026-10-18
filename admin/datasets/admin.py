"""Read-only panel views of datasets and their test cases."""

from fastadmin import register

from admin.core import ReadOnlyScopedAdmin
from components.datasets.models import Dataset, TestCase


@register(Dataset)
class DatasetAdmin(ReadOnlyScopedAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    ordering = ("-created_at",)


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
