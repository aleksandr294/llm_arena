"""Streaming JSON and CSV export of an experiment.

Rows are read in keyset-paginated batches (one short session each), so
memory is bounded by ``EXPORT_BATCH_SIZE`` rather than the experiment size.
Stored text is only passed through the ``json`` and ``csv`` encoders. Logs
carry the experiment id, format and row counts, never exported content.
"""

import csv
import io
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Text, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.schemas import Principal
from components.evaluation.models import EvaluationResult
from components.evaluation.schemas import parse_evaluator_config
from components.experiments.models import ExperimentResult
from components.experiments.repository import ExperimentRepository
from components.experiments.service import ExperimentNotFoundError
from components.metrics.schemas import ComparisonSchema
from components.metrics.service import MetricsService

logger = logging.getLogger(__name__)

EXPORT_BATCH_SIZE = 500
# Spreadsheets treat cells starting with these as formulas.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_R = ExperimentResult
Factory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class ExportRow:
    case_position: int
    provider: str
    model: str
    status: str
    attempts: int
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    error_kind: str | None
    evaluations: dict[str, str]
    rendered_prompt: str | None
    response_text: str | None


@dataclass(frozen=True)
class ExportContext:
    """Everything known before streaming starts, so a missing experiment is
    reported as 404 rather than as a broken stream."""

    experiment_id: uuid.UUID
    header: dict[str, Any]
    evaluator_ids: list[str]
    comparison: ComparisonSchema


def neutralize(value: str) -> str:
    """Prefix a single quote so spreadsheets do not run text as a formula."""
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


def _evaluator_ids(evaluator_config: dict[str, Any]) -> list[str]:
    try:
        config = parse_evaluator_config(evaluator_config)
    except ValidationError:
        return []
    return [e.id for e in config.evaluators]


async def prepare_export(
    factory: Factory,
    principal: Principal,
    experiment_id: uuid.UUID,
    pricing_file: str | None,
) -> ExportContext:
    """Raises ``ExperimentNotFoundError`` for a missing experiment or one
    outside the caller's scope, before anything is streamed. The comparison
    needs the whole experiment, so it is computed once here. Streaming
    itself runs on the validated context only."""
    async with factory() as session:
        experiment = await ExperimentRepository(session).get_scoped(
            experiment_id, principal
        )
        if experiment is None:
            raise ExperimentNotFoundError(experiment_id)
        comparison = await MetricsService(session, pricing_file).comparison(
            principal, experiment_id
        )
        header = {
            "experiment": {
                "id": str(experiment.id),
                "name": experiment.name,
                "dataset_id": str(experiment.dataset_id),
                "prompt_template": experiment.prompt_template,
                "config": experiment.config,
                "evaluator_config": experiment.evaluator_config,
            },
            "status": experiment.status,
            "finished": comparison.finished,
            "comparison": comparison.model_dump(mode="json"),
        }
        return ExportContext(
            experiment_id=experiment_id,
            header=header,
            evaluator_ids=_evaluator_ids(experiment.evaluator_config),
            comparison=comparison,
        )


async def iter_rows(
    factory: Factory,
    experiment_id: uuid.UUID,
    include_text: bool,
) -> AsyncIterator[list[ExportRow]]:
    """Batches ordered by ``(case_position, provider, model, id)``, resumed
    with a keyset so rows added meanwhile never shift the pages. Prompt and
    response text are selected only when ``include_text``."""
    last: tuple[int, str, str, uuid.UUID] | None = None
    while True:
        async with factory() as session:
            batch, last = await _read_batch(
                session, experiment_id, include_text, last
            )
        if not batch:
            return
        yield batch
        if len(batch) < EXPORT_BATCH_SIZE:
            return


async def _read_batch(
    session: AsyncSession,
    experiment_id: uuid.UUID,
    include_text: bool,
    after: tuple[int, str, str, uuid.UUID] | None,
) -> tuple[list[ExportRow], tuple[int, str, str, uuid.UUID] | None]:
    prompt = (
        _R.rendered_prompt
        if include_text
        else literal(None, type_=Text).label("rendered_prompt")
    )
    response = (
        _R.response_text
        if include_text
        else literal(None, type_=Text).label("response_text")
    )
    stmt = (
        select(
            _R.id,
            _R.case_position,
            _R.provider,
            _R.model,
            _R.status,
            _R.attempts,
            _R.latency_ms,
            _R.input_tokens,
            _R.output_tokens,
            _R.total_tokens,
            _R.error_kind,
            prompt,
            response,
        )
        .where(_R.experiment_id == experiment_id)
        .order_by(_R.case_position, _R.provider, _R.model, _R.id)
        .limit(EXPORT_BATCH_SIZE)
    )
    if after is not None:
        stmt = stmt.where(
            tuple_(_R.case_position, _R.provider, _R.model, _R.id)
            > tuple_(*after)
        )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return [], after
    evaluations: dict[uuid.UUID, dict[str, str]] = {}
    outcomes = await session.execute(
        select(
            EvaluationResult.experiment_result_id,
            EvaluationResult.evaluator_id,
            EvaluationResult.status,
        ).where(EvaluationResult.experiment_result_id.in_([r[0] for r in rows]))
    )
    for result_id, evaluator_id, status in outcomes.all():
        evaluations.setdefault(result_id, {})[evaluator_id] = status
    batch = [
        ExportRow(
            case_position=r[1],
            provider=r[2],
            model=r[3],
            status=r[4],
            attempts=r[5],
            latency_ms=r[6],
            input_tokens=r[7],
            output_tokens=r[8],
            total_tokens=r[9],
            error_kind=r[10],
            evaluations=evaluations.get(r[0], {}),
            rendered_prompt=r[11],
            response_text=r[12],
        )
        for r in rows
    ]
    tail = rows[-1]
    return batch, (tail[1], tail[2], tail[3], tail[0])


def _json_row(row: ExportRow, include_text: bool) -> dict[str, Any]:
    item: dict[str, Any] = {
        "case_position": row.case_position,
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "attempts": row.attempts,
        "latency_ms": row.latency_ms,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "total_tokens": row.total_tokens,
        "error_kind": row.error_kind,
        "evaluations": [
            {"evaluator_id": k, "status": v}
            for k, v in sorted(row.evaluations.items())
        ],
    }
    if include_text:
        item["rendered_prompt"] = row.rendered_prompt
        item["response_text"] = row.response_text
    return item


async def json_stream(
    factory: Factory, context: ExportContext, include_responses: bool
) -> AsyncIterator[str]:
    """A single JSON document: the header fields, then ``results`` written
    batch by batch. A failure mid-stream ends the stream without the closing
    brackets, so the truncated document does not parse."""
    count = 0
    try:
        head = json.dumps(context.header)
        yield head[:-1] + ', "results": ['
        async for batch in iter_rows(
            factory, context.experiment_id, include_responses
        ):
            chunk = ", ".join(
                json.dumps(_json_row(r, include_responses)) for r in batch
            )
            yield (", " if count else "") + chunk
            count += len(batch)
        yield "]}"
    except Exception as exc:
        logger.error(
            "export failed experiment_id=%s format=json rows=%d error=%s",
            context.experiment_id,
            count,
            type(exc).__name__,
        )
        return
    logger.info(
        "export done experiment_id=%s format=json rows=%d",
        context.experiment_id,
        count,
    )


def csv_columns(context: ExportContext, include_responses: bool) -> list[str]:
    columns = [
        "case_position",
        "provider",
        "model",
        "status",
        "attempts",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "error_kind",
        *(f"eval:{e}" for e in context.evaluator_ids),
    ]
    if include_responses:
        columns.append("response_text")
    return columns


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def _csv_row(
    row: ExportRow, evaluator_ids: list[str], include_responses: bool
) -> list[str]:
    cells = [
        _cell(row.case_position),
        neutralize(row.provider),
        neutralize(row.model),
        neutralize(row.status),
        _cell(row.attempts),
        _cell(row.latency_ms),
        _cell(row.input_tokens),
        _cell(row.output_tokens),
        _cell(row.total_tokens),
        neutralize(_cell(row.error_kind)),
        *(neutralize(_cell(row.evaluations.get(e))) for e in evaluator_ids),
    ]
    if include_responses:
        cells.append(neutralize(_cell(row.response_text)))
    return cells


def _render(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue()


async def csv_stream(
    factory: Factory, context: ExportContext, include_responses: bool
) -> AsyncIterator[str]:
    """Header row, then one row per result. Text cells starting with a
    formula character carry a leading single quote (documented alteration)."""
    count = 0
    try:
        yield _render([csv_columns(context, include_responses)])
        async for batch in iter_rows(
            factory, context.experiment_id, include_responses
        ):
            yield _render(
                [
                    _csv_row(r, context.evaluator_ids, include_responses)
                    for r in batch
                ]
            )
            count += len(batch)
    except Exception as exc:
        logger.error(
            "export failed experiment_id=%s format=csv rows=%d error=%s",
            context.experiment_id,
            count,
            type(exc).__name__,
        )
        return
    logger.info(
        "export done experiment_id=%s format=csv rows=%d",
        context.experiment_id,
        count,
    )
