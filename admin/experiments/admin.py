"""Read-only panel views of experiments and their generated results."""

from fastadmin import register

from admin.core import ReadOnlyScopedAdmin
from components.experiments.models import Experiment, ExperimentResult


@register(Experiment)
class ExperimentAdmin(ReadOnlyScopedAdmin):
    # The prompt template and the configuration JSON appear in the detail
    # view only.
    list_display = (
        "name",
        "status",
        "cancel_requested",
        "created_at",
        "started_at",
        "finished_at",
    )
    search_fields = ("name",)
    ordering = ("-created_at",)


@register(ExperimentResult)
class ExperimentResultAdmin(ReadOnlyScopedAdmin):
    # ``rendered_prompt``, ``response_text`` and ``error_message`` are model
    # and user content: detail view only.
    list_display = (
        "case_position",
        "provider",
        "model",
        "status",
        "attempts",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
        "error_kind",
        "started_at",
        "finished_at",
    )
    ordering = ("case_position",)
