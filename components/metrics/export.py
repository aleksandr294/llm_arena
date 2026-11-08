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
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

from pydantic import ValidationError
from sqlalchemy import Select, Text, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from components.core.database import SessionScope
from components.core.schemas import Principal
from components.evaluation.models import EvaluationResult
from components.evaluation.schemas import parse_evaluator_config
from components.experiments.exceptions import ExperimentNotFoundError
from components.experiments.models import Experiment, ExperimentResult
from components.metrics.schemas import ComparisonSchema
from components.metrics.service import MetricsService
from components.metrics.utils import neutralize

logger = logging.getLogger(__name__)

EXPORT_BATCH_SIZE = 500
_R = ExperimentResult
MetricsServiceFactory = Callable[[AsyncSession], MetricsService]


class _ResultRecord(NamedTuple):
    """One selected ``experiment_results`` row, in select order."""

    id: uuid.UUID
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
    response_truncated: bool
    rendered_prompt: str | None
    response_text: str | None


# ``(case_position, provider, model, id)`` of the last row sent.
_Cursor = tuple[int, str, str, uuid.UUID]


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
    response_truncated: bool
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


def _evaluator_ids(evaluator_config: dict[str, Any]) -> list[str]:
    try:
        config = parse_evaluator_config(evaluator_config)
    except ValidationError:
        return []
    return [e.id for e in config.evaluators]


async def prepare_export(
    scope: SessionScope,
    principal: Principal,
    experiment_id: uuid.UUID,
    metrics_service: MetricsServiceFactory,
) -> ExportContext:
    """Raises ``ExperimentNotFoundError`` for a missing experiment or one
    outside the caller's scope, before anything is streamed. The comparison
    needs the whole experiment, so it is computed once here. Streaming
    itself runs on the validated context only. ``scope`` opens the sessions
    (injected by the endpoint); ``metrics_service`` builds the metrics
    collaborator for the session opened here."""
    async with scope() as session:
        # The metrics service call is scoped (it raises for a missing or
        # out-of-scope experiment), so the plain load below is safe.
        comparison = await metrics_service(session).comparison(
            principal, experiment_id
        )
        experiment = await session.get(Experiment, experiment_id)
        if experiment is None:  # deleted between the two reads
            raise ExperimentNotFoundError(experiment_id)
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
    scope: SessionScope,
    experiment_id: uuid.UUID,
    include_text: bool,
) -> AsyncIterator[list[ExportRow]]:
    """Batches ordered by ``(case_position, provider, model, id)``, resumed
    with a keyset so rows added meanwhile never shift the pages. Prompt and
    response text are selected only when ``include_text``."""
    last: _Cursor | None = None
    while True:
        async with scope() as session:
            batch, last = await _read_batch(
                session, experiment_id, include_text, last
            )
        if not batch:
            return
        yield batch
        if len(batch) < EXPORT_BATCH_SIZE:
            return


def _batch_statement(
    experiment_id: uuid.UUID, include_text: bool, after: _Cursor | None
) -> Select[Any]:
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
            _R.response_truncated,
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
    return stmt  # type: ignore[return-value]


async def _read_batch(
    session: AsyncSession,
    experiment_id: uuid.UUID,
    include_text: bool,
    after: _Cursor | None,
) -> tuple[list[ExportRow], _Cursor | None]:
    stmt = _batch_statement(experiment_id, include_text, after)
    rows = [_ResultRecord(*r) for r in (await session.execute(stmt)).all()]
    if not rows:
        return [], after
    evaluations: defaultdict[uuid.UUID, dict[str, str]] = defaultdict(dict)
    outcomes = await session.execute(
        select(
            EvaluationResult.experiment_result_id,
            EvaluationResult.evaluator_id,
            EvaluationResult.status,
        ).where(EvaluationResult.experiment_result_id.in_([r.id for r in rows]))
    )
    for result_id, evaluator_id, status in outcomes.all():
        evaluations[result_id][evaluator_id] = status
    batch = [
        ExportRow(
            case_position=r.case_position,
            provider=r.provider,
            model=r.model,
            status=r.status,
            attempts=r.attempts,
            latency_ms=r.latency_ms,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            total_tokens=r.total_tokens,
            error_kind=r.error_kind,
            response_truncated=r.response_truncated,
            evaluations=evaluations.get(r.id, {}),
            rendered_prompt=r.rendered_prompt,
            response_text=r.response_text,
        )
        for r in rows
    ]
    tail = rows[-1]
    return batch, (tail.case_position, tail.provider, tail.model, tail.id)


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
        "response_truncated": row.response_truncated,
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
    scope: SessionScope, context: ExportContext, include_responses: bool
) -> AsyncIterator[str]:
    """A single JSON document: the header fields, then ``results`` written
    batch by batch. A failure mid-stream is logged (no row content) and
    re-raised, so the server aborts the response instead of ending it as if it
    were complete; the document is also left without its closing brackets."""
    count = 0
    try:
        head = json.dumps(context.header)
        yield head[:-1] + ', "results": ['
        async for batch in iter_rows(
            scope, context.experiment_id, include_responses
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
        raise
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
        "response_truncated",
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
        "true" if row.response_truncated else "false",
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
    scope: SessionScope, context: ExportContext, include_responses: bool
) -> AsyncIterator[str]:
    """Header row, then one row per result. Text cells starting with a
    formula character carry a leading single quote (documented alteration). A
    failure mid-stream is logged and re-raised so the response is aborted
    rather than ending as a short but valid-looking CSV."""
    count = 0
    try:
        yield _render([csv_columns(context, include_responses)])
        async for batch in iter_rows(
            scope, context.experiment_id, include_responses
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
        raise
    logger.info(
        "export done experiment_id=%s format=csv rows=%d",
        context.experiment_id,
        count,
    )
