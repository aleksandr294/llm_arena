"""Read-only panel view of evaluator outcomes."""

from fastadmin import register

from admin.core import ReadOnlyScopedAdmin
from components.evaluation.models import EvaluationResult


@register(EvaluationResult)
class EvaluationResultAdmin(ReadOnlyScopedAdmin):
    # The ``details`` JSON appears in the detail view only.
    list_display = (
        "evaluator_id",
        "evaluator_type",
        "evaluator_version",
        "status",
        "created_at",
    )
    ordering = ("-created_at",)
