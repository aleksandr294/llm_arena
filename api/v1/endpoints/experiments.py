import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from components.core.config import get_settings
from components.core.database import get_session
from components.datasets.service import DatasetNotFoundError
from components.experiments.models import ResultStatus
from components.experiments.schemas import (
    ExperimentCreateSchema,
    ExperimentDetailSchema,
    ExperimentEnqueueSchema,
    ExperimentPageSchema,
    ExperimentResultPageSchema,
    ExperimentResultSchema,
    ExperimentSchema,
)
from components.experiments.service import (
    EnqueueResults,
    ExperimentNotFoundError,
    ExperimentService,
    ExperimentValidationError,
    InvalidTransitionError,
)
from components.experiments.tasks import submit_results
from components.third_party.llm.service import LLMService

router = APIRouter(prefix="/experiments", tags=["experiments"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def get_enqueue() -> EnqueueResults:
    """Overridable in tests so no broker is needed."""
    return submit_results


def get_llm_service() -> LLMService:
    return LLMService(get_settings())


def get_experiment_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    llm: Annotated[LLMService, Depends(get_llm_service)],
    enqueue: Annotated[EnqueueResults, Depends(get_enqueue)],
) -> ExperimentService:
    return ExperimentService(session, llm, enqueue)


ServiceDep = Annotated[ExperimentService, Depends(get_experiment_service)]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Experiment not found")


def _conflict(exc: InvalidTransitionError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=ExperimentSchema
)
async def create_experiment(
    body: ExperimentCreateSchema, service: ServiceDep
) -> ExperimentSchema:
    try:
        experiment = await service.create(body)
    except DatasetNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Dataset not found"
        ) from None
    except ExperimentValidationError as exc:
        # Same shape as FastAPI validation errors.
        raise HTTPException(
            422,  # constant name differs across Starlette versions
            [
                {
                    "loc": ["body", *exc.field.split(".")],
                    "msg": exc.message,
                    "type": "value_error",
                }
            ],
        ) from None
    return ExperimentSchema.model_validate(experiment)


@router.get("", response_model=ExperimentPageSchema)
async def list_experiments(
    service: ServiceDep, limit: Limit = 50, offset: Offset = 0
) -> ExperimentPageSchema:
    items, total = await service.list_experiments(limit, offset)
    return ExperimentPageSchema(
        items=[ExperimentSchema.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{experiment_id}", response_model=ExperimentDetailSchema)
async def get_experiment(
    experiment_id: uuid.UUID, service: ServiceDep
) -> ExperimentDetailSchema:
    try:
        experiment, counts = await service.get(experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None
    return ExperimentDetailSchema(
        **ExperimentSchema.model_validate(experiment).model_dump(),
        status_counts=counts,
    )


@router.get(
    "/{experiment_id}/results", response_model=ExperimentResultPageSchema
)
async def list_results(
    experiment_id: uuid.UUID,
    service: ServiceDep,
    limit: Limit = 50,
    offset: Offset = 0,
    result_status: Annotated[ResultStatus | None, Query(alias="status")] = None,
) -> ExperimentResultPageSchema:
    try:
        items, total = await service.list_results(
            experiment_id,
            limit,
            offset,
            result_status.value if result_status else None,
        )
    except ExperimentNotFoundError:
        raise _not_found() from None
    return ExperimentResultPageSchema(
        items=[ExperimentResultSchema.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _enqueue_response(
    service: ExperimentService, experiment_id: uuid.UUID, enqueued: int
) -> ExperimentEnqueueSchema:
    experiment, _ = await service.get(experiment_id)
    return ExperimentEnqueueSchema(
        experiment=ExperimentSchema.model_validate(experiment),
        enqueued=enqueued,
    )


@router.post(
    "/{experiment_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExperimentEnqueueSchema,
)
async def start_experiment(
    experiment_id: uuid.UUID, service: ServiceDep
) -> ExperimentEnqueueSchema:
    try:
        enqueued = await service.start(experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None
    except InvalidTransitionError as exc:
        raise _conflict(exc) from None
    return await _enqueue_response(service, experiment_id, enqueued)


@router.post("/{experiment_id}/cancel", response_model=ExperimentSchema)
async def cancel_experiment(
    experiment_id: uuid.UUID, service: ServiceDep
) -> ExperimentSchema:
    try:
        experiment = await service.cancel(experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None
    except InvalidTransitionError as exc:
        raise _conflict(exc) from None
    return ExperimentSchema.model_validate(experiment)


@router.post(
    "/{experiment_id}/retry-failed",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExperimentEnqueueSchema,
)
async def retry_failed(
    experiment_id: uuid.UUID, service: ServiceDep
) -> ExperimentEnqueueSchema:
    try:
        enqueued = await service.retry_failed(experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None
    except InvalidTransitionError as exc:
        raise _conflict(exc) from None
    return await _enqueue_response(service, experiment_id, enqueued)
