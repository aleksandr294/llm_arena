import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.v1.docs import errors
from api.v1.utils import (
    CreatedRangeSchema,
    SessionDep,
    get_created_range,
    get_principal,
)
from components.core.config import get_settings
from components.core.exceptions import OrganizationRequiredError
from components.core.schemas import Principal
from components.datasets.exceptions import DatasetNotFoundError
from components.experiments.dependencies import build_experiment_service
from components.experiments.enums import ExperimentStatus, ResultStatus
from components.experiments.exceptions import (
    ExperimentNotFoundError,
    ExperimentValidationError,
    InvalidTransitionError,
)
from components.experiments.schemas import (
    ExperimentCreateSchema,
    ExperimentDetailSchema,
    ExperimentEnqueueSchema,
    ExperimentPageSchema,
    ExperimentResultPageSchema,
    ExperimentResultSchema,
    ExperimentSchema,
)
from components.experiments.service import EnqueueResults, ExperimentService
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
    session: SessionDep,
    llm: Annotated[LLMService, Depends(get_llm_service)],
    enqueue: Annotated[EnqueueResults, Depends(get_enqueue)],
) -> ExperimentService:
    return build_experiment_service(session, llm, enqueue)


ServiceDep = Annotated[ExperimentService, Depends(get_experiment_service)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Experiment not found")


def _conflict(exc: InvalidTransitionError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ExperimentSchema,
    summary="Create an experiment",
    responses=errors(401, 403, 404),
)
async def create_experiment(
    body: ExperimentCreateSchema, service: ServiceDep, principal: PrincipalDep
) -> ExperimentSchema:
    try:
        experiment = await service.create(principal, body)
    except OrganizationRequiredError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
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


@router.get(
    "",
    response_model=ExperimentPageSchema,
    summary="List experiments",
    responses=errors(401),
)
async def list_experiments(
    service: ServiceDep,
    principal: PrincipalDep,
    created: Annotated[CreatedRangeSchema, Depends(get_created_range)],
    limit: Limit = 50,
    offset: Offset = 0,
    experiment_status: Annotated[
        ExperimentStatus | None, Query(alias="status")
    ] = None,
    owner_id: uuid.UUID | None = None,
) -> ExperimentPageSchema:
    items, total = await service.list_experiments(
        principal,
        limit,
        offset,
        experiment_status.value if experiment_status else None,
        created.created_from,
        created.created_to,
        owner_id,
    )
    return ExperimentPageSchema(
        items=[ExperimentSchema.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{experiment_id}",
    response_model=ExperimentDetailSchema,
    summary="Get an experiment",
    responses=errors(401, 404),
)
async def get_experiment(
    experiment_id: uuid.UUID, service: ServiceDep, principal: PrincipalDep
) -> ExperimentDetailSchema:
    try:
        experiment, counts = await service.get(principal, experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None
    return ExperimentDetailSchema(
        **ExperimentSchema.model_validate(experiment).model_dump(),
        status_counts=counts,
    )


@router.get(
    "/{experiment_id}/results",
    response_model=ExperimentResultPageSchema,
    summary="List experiment results",
    responses=errors(401, 404),
)
async def list_results(
    experiment_id: uuid.UUID,
    service: ServiceDep,
    principal: PrincipalDep,
    limit: Limit = 50,
    offset: Offset = 0,
    result_status: Annotated[ResultStatus | None, Query(alias="status")] = None,
) -> ExperimentResultPageSchema:
    try:
        items, total = await service.list_results(
            principal,
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
    service: ExperimentService,
    principal: Principal,
    experiment_id: uuid.UUID,
    enqueued: int,
) -> ExperimentEnqueueSchema:
    experiment, _ = await service.get(principal, experiment_id)
    return ExperimentEnqueueSchema(
        experiment=ExperimentSchema.model_validate(experiment),
        enqueued=enqueued,
    )


@router.post(
    "/{experiment_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExperimentEnqueueSchema,
    summary="Start an experiment",
    description="Enqueue its cases. 202 means submitted, not executed.",
    responses=errors(401, 404, 409),
)
async def start_experiment(
    experiment_id: uuid.UUID, service: ServiceDep, principal: PrincipalDep
) -> ExperimentEnqueueSchema:
    try:
        enqueued = await service.start(principal, experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None
    except InvalidTransitionError as exc:
        raise _conflict(exc) from None
    return await _enqueue_response(service, principal, experiment_id, enqueued)


@router.post(
    "/{experiment_id}/cancel",
    response_model=ExperimentSchema,
    summary="Cancel an experiment",
    responses=errors(401, 404, 409),
)
async def cancel_experiment(
    experiment_id: uuid.UUID, service: ServiceDep, principal: PrincipalDep
) -> ExperimentSchema:
    try:
        experiment = await service.cancel(principal, experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None
    except InvalidTransitionError as exc:
        raise _conflict(exc) from None
    return ExperimentSchema.model_validate(experiment)


@router.post(
    "/{experiment_id}/retry-failed",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExperimentEnqueueSchema,
    summary="Retry failed cases",
    description="Enqueue only the failed cases; successful ones are not rerun.",
    responses=errors(401, 404, 409),
)
async def retry_failed(
    experiment_id: uuid.UUID, service: ServiceDep, principal: PrincipalDep
) -> ExperimentEnqueueSchema:
    try:
        enqueued = await service.retry_failed(principal, experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None
    except InvalidTransitionError as exc:
        raise _conflict(exc) from None
    return await _enqueue_response(service, principal, experiment_id, enqueued)
