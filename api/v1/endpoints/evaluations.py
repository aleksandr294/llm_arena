import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.v1.docs import errors
from api.v1.utils import SessionDep, get_principal
from components.core.schemas import Principal
from components.evaluation.dependencies import build_evaluation_service
from components.evaluation.enums import OutcomeStatus
from components.evaluation.schemas import (
    EvaluationEnqueueSchema,
    EvaluationPageSchema,
    EvaluationSummarySchema,
)
from components.evaluation.service import EvaluationService
from components.evaluation.tasks import submit_results
from components.experiments.exceptions import ExperimentNotFoundError
from components.experiments.service import EnqueueResults

router = APIRouter(prefix="/experiments", tags=["evaluations"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def get_enqueue() -> EnqueueResults:
    """Overridable in tests so no broker is needed."""
    return submit_results


def get_evaluation_service(session: SessionDep) -> EvaluationService:
    return build_evaluation_service(session)


ServiceDep = Annotated[EvaluationService, Depends(get_evaluation_service)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Experiment not found")


@router.post(
    "/{experiment_id}/evaluate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EvaluationEnqueueSchema,
    summary="Evaluate an experiment",
    responses=errors(401, 404),
)
async def evaluate_experiment(
    experiment_id: uuid.UUID,
    service: ServiceDep,
    principal: PrincipalDep,
    enqueue: Annotated[EnqueueResults, Depends(get_enqueue)],
) -> EvaluationEnqueueSchema:
    """Enqueue evaluation of succeeded results that lack some configured
    evaluation. 202 means submitted, not evaluated."""
    try:
        result_ids, remaining = await service.backfill(principal, experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None
    if result_ids:
        await enqueue(result_ids)
    return EvaluationEnqueueSchema(
        experiment_id=experiment_id,
        enqueued=len(result_ids),
        remaining=remaining,
    )


@router.get(
    "/{experiment_id}/evaluations/summary",
    response_model=EvaluationSummarySchema,
    summary="Summarize evaluations",
    responses=errors(401, 404),
)
async def evaluation_summary(
    experiment_id: uuid.UUID, service: ServiceDep, principal: PrincipalDep
) -> EvaluationSummarySchema:
    try:
        return await service.summary(principal, experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None


@router.get(
    "/{experiment_id}/evaluations",
    response_model=EvaluationPageSchema,
    summary="List evaluation results",
    responses=errors(401, 404),
)
async def list_evaluations(
    experiment_id: uuid.UUID,
    service: ServiceDep,
    principal: PrincipalDep,
    limit: Limit = 50,
    offset: Offset = 0,
    evaluator_id: str | None = None,
    outcome: Annotated[OutcomeStatus | None, Query(alias="status")] = None,
) -> EvaluationPageSchema:
    try:
        items, total = await service.list_evaluations(
            principal,
            experiment_id,
            limit,
            offset,
            evaluator_id,
            outcome.value if outcome else None,
        )
    except ExperimentNotFoundError:
        raise _not_found() from None
    return EvaluationPageSchema(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
