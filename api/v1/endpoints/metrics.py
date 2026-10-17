import uuid
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.config import get_settings
from components.core.database import get_session, get_session_factory
from components.experiments.service import ExperimentNotFoundError
from components.metrics.export import csv_stream, json_stream, prepare_export
from components.metrics.schemas import (
    CasePageSchema,
    ComparisonSchema,
    SummarySchema,
)
from components.metrics.service import MetricsService

router = APIRouter(prefix="/experiments", tags=["metrics"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


class ExportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"


def get_pricing_file() -> str | None:
    """Overridable in tests; ``PRICING_FILE`` from settings."""
    return get_settings().pricing_file


def get_factory() -> async_sessionmaker[AsyncSession]:
    """Overridable in tests; the export opens a session per batch."""
    return get_session_factory()


def get_metrics_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    pricing_file: Annotated[str | None, Depends(get_pricing_file)],
) -> MetricsService:
    return MetricsService(session, pricing_file)


ServiceDep = Annotated[MetricsService, Depends(get_metrics_service)]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Experiment not found")


@router.get("/{experiment_id}/summary", response_model=SummarySchema)
async def metrics_summary(
    experiment_id: uuid.UUID, service: ServiceDep
) -> SummarySchema:
    try:
        return await service.summary(experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None


@router.get("/{experiment_id}/comparison", response_model=ComparisonSchema)
async def metrics_comparison(
    experiment_id: uuid.UUID, service: ServiceDep
) -> ComparisonSchema:
    try:
        return await service.comparison(experiment_id)
    except ExperimentNotFoundError:
        raise _not_found() from None


@router.get("/{experiment_id}/cases", response_model=CasePageSchema)
async def metrics_cases(
    experiment_id: uuid.UUID,
    service: ServiceDep,
    limit: Limit = 50,
    offset: Offset = 0,
    include_responses: bool = False,
) -> CasePageSchema:
    try:
        return await service.cases(
            experiment_id, limit, offset, include_responses
        )
    except ExperimentNotFoundError:
        raise _not_found() from None


@router.get("/{experiment_id}/export")
async def export_experiment(
    experiment_id: uuid.UUID,
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_factory)],
    pricing_file: Annotated[str | None, Depends(get_pricing_file)],
    export_format: Annotated[
        ExportFormat, Query(alias="format")
    ] = ExportFormat.JSON,
    include_responses: bool | None = None,
) -> StreamingResponse:
    """Streams the experiment; the comparison is computed before the first
    byte, so an unknown experiment is a plain 404. A failure after that
    truncates the stream (status 200 is already sent)."""
    try:
        context = await prepare_export(factory, experiment_id, pricing_file)
    except ExperimentNotFoundError:
        raise _not_found() from None
    is_json = export_format is ExportFormat.JSON
    # Responses are on by default for JSON and off for CSV.
    include = is_json if include_responses is None else include_responses
    stream: AsyncIterator[str] = (
        json_stream(factory, context, include)
        if is_json
        else csv_stream(factory, context, include)
    )
    return StreamingResponse(
        stream,
        media_type=(
            "application/json" if is_json else "text/csv; charset=utf-8"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="experiment-{experiment_id}'
                f'.{export_format.value}"'
            )
        },
    )
