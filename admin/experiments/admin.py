"""Panel views of experiments and their generated results. Results are
read-only; a finished experiment can be deleted by admins and superadmins."""

import uuid
from collections.abc import Sequence
from typing import Any, ClassVar

from fastadmin import AdminApiException, register
from fastadmin.models.schemas import WidgetType
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.core import ReadOnlyScopedAdmin, principal_of
from components.core.config import get_settings
from components.core.database import session_scope
from components.core.enums import Role
from components.datasets.models import Dataset
from components.experiments.dependencies import build_experiment_service
from components.experiments.enums import ExperimentStatus
from components.experiments.exceptions import ExperimentNotFoundError
from components.experiments.models import Experiment, ExperimentResult
from components.experiments.repository import ExperimentResultRepository
from components.third_party.llm.service import LLMService
from components.users.models import User

ACTIVE_STATUSES = (
    ExperimentStatus.PENDING.value,
    ExperimentStatus.RUNNING.value,
)


async def _no_enqueue(result_ids: Sequence[uuid.UUID]) -> None:
    # Deleting never schedules work; reaching this is a programming error.
    raise RuntimeError("the panel does not enqueue experiment work")


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
    computed_fields: ClassVar[dict[str, WidgetType]] = {
        "dataset_name": WidgetType.Input,
        "owner_email": WidgetType.Input,
        "results_by_status": WidgetType.JsonTextArea,
    }

    async def has_delete_permission(self, user_id: Any = None) -> bool:
        principal = principal_of(self.user)
        if principal is None or principal.role is Role.STAFF:
            return False
        await self._refuse_unavailable_delete()
        return True

    async def _refuse_unavailable_delete(self) -> None:
        """fastadmin turns every error raised by ``delete_model`` into a 500,
        so the answers a user needs (not found, still active) are given here,
        where an ``AdminApiException`` reaches the client unchanged. The
        permission hook also runs when the interface asks for its
        configuration; only a DELETE request carries an experiment id. The
        authoritative check is ``ExperimentService.delete``, under a row
        lock, so a race between this read and the delete still removes
        nothing."""
        request = self.request
        if request is None or request.method != "DELETE":
            return
        try:
            experiment_id = uuid.UUID(str(request.path_params.get("id")))
        except ValueError:
            raise AdminApiException(
                404, detail="Experiment not found."
            ) from None
        async with self.get_sessionmaker()() as session:
            status = await session.scalar(
                select(Experiment.status).where(Experiment.id == experiment_id)
            )
        if status is None:
            raise AdminApiException(404, detail="Experiment not found.")
        if status in ACTIVE_STATUSES:
            raise AdminApiException(
                409,
                detail=(
                    "The experiment is still active. Cancel it and wait "
                    "until it has finished before deleting it."
                ),
            )

    async def delete_model(self, id: Any) -> None:
        caller = principal_of(self.user)
        if caller is None or caller.role is Role.STAFF:
            raise PermissionError("not allowed")
        try:
            experiment_id = (
                id if isinstance(id, uuid.UUID) else uuid.UUID(str(id))
            )
            async with session_scope() as session:
                await build_experiment_service(
                    session, LLMService(get_settings()), _no_enqueue
                ).delete(caller, experiment_id)
        except (ValueError, ExperimentNotFoundError):
            return  # already gone, or not visible: nothing to delete

    async def computed_values(
        self, session: AsyncSession, obj: Any
    ) -> dict[str, Any]:
        owner = None
        if obj.owner_id is not None:
            owner = await session.scalar(
                select(User.email).where(User.id == obj.owner_id)
            )
        return {
            "dataset_name": await session.scalar(
                select(Dataset.name).where(Dataset.id == obj.dataset_id)
            ),
            "owner_email": owner,
            "results_by_status": await ExperimentResultRepository(
                session
            ).status_counts(obj.id),
        }


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
    computed_fields: ClassVar[dict[str, WidgetType]] = {
        "experiment_name": WidgetType.Input
    }

    async def computed_values(
        self, session: AsyncSession, obj: Any
    ) -> dict[str, Any]:
        # ``case_position`` is a column and already part of the detail.
        name = await session.scalar(
            select(Experiment.name).where(Experiment.id == obj.experiment_id)
        )
        return {"experiment_name": name}
