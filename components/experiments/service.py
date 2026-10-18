import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from components.core.access import require_organization
from components.core.database import commit, on_commit
from components.core.schemas import Principal
from components.datasets.exceptions import DatasetNotFoundError
from components.datasets.repository import (
    DatasetRepository,
    TestCaseRepository,
)
from components.experiments.enums import ExperimentStatus, ResultStatus
from components.experiments.exceptions import (
    ExperimentNotFoundError,
    ExperimentValidationError,
    InvalidTransitionError,
)
from components.experiments.models import Experiment, ExperimentResult
from components.experiments.repository import (
    ExperimentRepository,
    ExperimentResultRepository,
    finalize_experiment,
)
from components.experiments.schemas import ExperimentCreateSchema
from components.experiments.utils import render_prompt
from components.third_party.llm.exceptions import (
    ProviderNotConfiguredError,
    UnsupportedProviderError,
)
from components.third_party.llm.service import LLMService

MAX_RESULTS = 10_000

# Port for the task layer: receives the ids of results to execute. Kept as a
# plain callable so the service never imports Taskiq.
EnqueueResults = Callable[[Sequence[uuid.UUID]], Awaitable[None]]

_FINAL_FOR_START = {
    ExperimentStatus.COMPLETED.value,
    ExperimentStatus.PARTIALLY_FAILED.value,
    ExperimentStatus.FAILED.value,
    ExperimentStatus.CANCELLED.value,
}
_RETRYABLE_STATUSES = {
    ExperimentStatus.PARTIALLY_FAILED.value,
    ExperimentStatus.FAILED.value,
}


class ExperimentService:
    """API-side orchestration. Uses the caller's session; the entry point
    owns the transaction. ``start`` and ``retry_failed`` are the explicit
    commit points: the state change must be durable before tasks are
    enqueued (a worker must never see an uncommitted experiment, and a
    rolled-back request must not leave orphan tasks), so they register the
    enqueue with ``on_commit`` and commit."""

    def __init__(
        self,
        session: AsyncSession,
        llm_service: LLMService,
        enqueue: EnqueueResults,
    ) -> None:
        self.session = session
        self.llm = llm_service
        self.enqueue = enqueue
        self.datasets = DatasetRepository(session)
        self.cases = TestCaseRepository(session)
        self.experiments = ExperimentRepository(session)
        self.results = ExperimentResultRepository(session)

    def _check_providers(self, data: ExperimentCreateSchema) -> None:
        for target in data.config.targets:
            try:
                self.llm.ensure_provider_ready(target.provider)
            except (
                ProviderNotConfiguredError,
                UnsupportedProviderError,
            ) as exc:
                raise ExperimentValidationError(
                    "config.targets", str(exc)
                ) from exc

    async def create(
        self, principal: Principal, data: ExperimentCreateSchema
    ) -> Experiment:
        """Create the experiment with every result frozen as pending, with
        its prompt already rendered. No model request is made."""
        # Locking the dataset serializes against concurrent case inserts so
        # the frozen case set is well defined.
        # A dataset outside the caller's scope is reported as unknown, and
        # the caller's organization must exist before anything is stored.
        require_organization(principal)
        dataset = await self.datasets.get_for_update(data.dataset_id, principal)
        if dataset is None:
            raise DatasetNotFoundError(data.dataset_id)
        self._check_providers(data)
        targets = data.config.targets
        case_count = await self.cases.count(data.dataset_id)
        if case_count == 0:
            raise ExperimentValidationError(
                "dataset_id", "dataset has no test cases"
            )
        if case_count * len(targets) > MAX_RESULTS:
            raise ExperimentValidationError(
                "config.targets",
                f"experiment would create more than {MAX_RESULTS} results",
            )
        cases, _ = await self.cases.list_page(data.dataset_id, case_count, 0)
        experiment = Experiment(
            name=data.name,
            dataset_id=data.dataset_id,
            # Owner comes from the caller, never from the request body; the
            # organization is the dataset's, which the caller can see.
            organization_id=dataset.organization_id,
            owner_id=principal.user_id,
            prompt_template=data.prompt_template,
            config=data.config.model_dump(mode="json"),
            # Normalized dump: defaults are frozen with the experiment.
            evaluator_config=data.evaluator_config.model_dump(mode="json"),
        )
        await self.experiments.add(experiment)
        await self.results.add_many(
            [
                ExperimentResult(
                    experiment_id=experiment.id,
                    test_case_id=case.id,
                    case_position=case.position,
                    provider=target.provider.value,
                    model=target.model,
                    rendered_prompt=render_prompt(
                        data.prompt_template, case.input, case.expected_output
                    ),
                )
                for case in cases
                for target in targets
            ]
        )
        await self.session.refresh(experiment)
        return experiment

    async def _locked(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> Experiment:
        experiment = await self.experiments.get_for_update(
            experiment_id, principal
        )
        if experiment is None:
            raise ExperimentNotFoundError(experiment_id)
        return experiment

    async def start(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> int:
        """Move pending to running and enqueue the pending results.

        Starting a running experiment enqueues its still pending results
        and its stale running ones (lease expired, crashed worker) again
        (recovery from lost messages and crashes). Returns the number
        enqueued.
        """
        experiment = await self._locked(principal, experiment_id)
        if experiment.status in _FINAL_FOR_START:
            raise InvalidTransitionError(
                experiment_id, experiment.status, "start"
            )
        if experiment.status == ExperimentStatus.PENDING.value:
            experiment.status = ExperimentStatus.RUNNING.value
            experiment.started_at = datetime.now(UTC)
        pending = await self.results.list_ids_by_status(
            experiment_id, ResultStatus.PENDING
        )
        stale = await self.results.list_stale_running_ids(experiment_id)
        if not pending and not stale:
            # Repairs a finalization lost to a crash.
            await finalize_experiment(experiment, self.results)
        return await self._commit_and_enqueue([*pending, *stale])

    async def cancel(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> Experiment:
        """Cancel pending results; running ones finish and are stored, then
        the finalizer sets ``cancelled``. Repeating it while results are
        still running is harmless."""
        experiment = await self._locked(principal, experiment_id)
        if experiment.status not in (
            ExperimentStatus.PENDING.value,
            ExperimentStatus.RUNNING.value,
        ):
            raise InvalidTransitionError(
                experiment_id, experiment.status, "cancel"
            )
        experiment.cancel_requested = True
        await self.results.cancel_pending(experiment_id)
        await finalize_experiment(experiment, self.results)
        await self.session.flush()
        return experiment

    async def retry_failed(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> int:
        """Re-run failed results only. Returns the number enqueued."""
        experiment = await self._locked(principal, experiment_id)
        if experiment.status not in _RETRYABLE_STATUSES:
            raise InvalidTransitionError(
                experiment_id, experiment.status, "retry"
            )
        ids = await self.results.reset_failed(experiment_id)
        experiment.status = ExperimentStatus.RUNNING.value
        experiment.finished_at = None
        # Nothing to run (no failed results left) must not leave the
        # experiment running forever.
        await finalize_experiment(experiment, self.results)
        return await self._commit_and_enqueue(ids)

    async def _commit_and_enqueue(self, ids: Sequence[uuid.UUID]) -> int:
        if ids:

            async def publish() -> None:
                await self.enqueue(ids)

            on_commit(self.session, publish)
        # The single commit of the operation; the enqueue runs right after
        # it, so a failed enqueue leaves committed pending results that a
        # repeated start enqueues again.
        await commit(self.session)
        return len(ids)

    async def get(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> tuple[Experiment, dict[str, int]]:
        experiment = await self.experiments.get_scoped(experiment_id, principal)
        if experiment is None:
            raise ExperimentNotFoundError(experiment_id)
        return experiment, await self.results.status_counts(experiment_id)

    async def list_experiments(
        self,
        principal: Principal,
        limit: int,
        offset: int,
        status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> tuple[Sequence[Experiment], int]:
        return await self.experiments.list_page(
            principal,
            limit,
            offset,
            status,
            created_from,
            created_to,
            owner_id,
        )

    async def list_results(
        self,
        principal: Principal,
        experiment_id: uuid.UUID,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[Sequence[ExperimentResult], int]:
        if await self.experiments.get_scoped(experiment_id, principal) is None:
            raise ExperimentNotFoundError(experiment_id)
        return await self.results.list_page(
            experiment_id, limit, offset, status
        )
