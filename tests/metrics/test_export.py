"""Export generators over seeded data: need PostgreSQL (``DATABASE_URL``)."""

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.database import SessionScope, session_scope
from components.core.enums import Role
from components.experiments.exceptions import ExperimentNotFoundError
from components.experiments.models import Experiment
from components.metrics import export
from components.metrics.dependencies import build_metrics_service
from components.metrics.export import csv_stream, json_stream, prepare_export
from components.metrics.service import MetricsService
from components.metrics.utils import neutralize
from tests.access import ROOT, seed_principal
from tests.metrics.helpers import Row, seed

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


def _scope(factory: Factory) -> SessionScope:
    return partial(session_scope, factory)


def _metrics(session: AsyncSession) -> MetricsService:
    return build_metrics_service(session)


TRICKY = 'a,b "quoted"\nline2 é中\U0001f600'


async def _collect(stream: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in stream]


async def _json(
    factory: Factory, experiment_id: uuid.UUID, include: bool = True
) -> tuple[dict[str, Any], int]:
    context = await prepare_export(
        _scope(factory), ROOT, experiment_id, _metrics
    )
    chunks = await _collect(json_stream(_scope(factory), context, include))
    return json.loads("".join(chunks)), len(chunks)


async def _csv(
    factory: Factory, experiment_id: uuid.UUID, include: bool = False
) -> list[list[str]]:
    context = await prepare_export(
        _scope(factory), ROOT, experiment_id, _metrics
    )
    chunks = await _collect(csv_stream(_scope(factory), context, include))
    return list(csv.reader(io.StringIO("".join(chunks), newline="")))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=SUM(A1)", "'=SUM(A1)"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@x", "'@x"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        ("plain", "plain"),
        ("", ""),
        ("a=b", "a=b"),
    ],
)
def test_neutralize(value: str, expected: str) -> None:
    assert neutralize(value) == expected


async def test_json_export_round_trips_and_spans_batches(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "EXPORT_BATCH_SIZE", 2)
    rows = [
        Row(
            p,
            model=m,
            latency=10.0 * p,
            tokens=(p, p, 2 * p),
            text=TRICKY if p == 3 else f"r{p}",
            evals={"ex": "passed"},
        )
        for p in range(1, 6)
        for m in ("m1", "m2")
    ]
    experiment_id = await seed(
        session_factory, rows, models=("m1", "m2"), evaluators=("ex",)
    )
    doc, chunks = await _json(session_factory, experiment_id)
    assert chunks > 3  # header + several batches + trailer
    results = doc["results"]
    assert len(results) == 10
    keys = [(r["case_position"], r["model"]) for r in results]
    assert keys == sorted(keys)
    assert len(set(keys)) == 10
    assert doc["finished"] is True
    assert doc["status"] == "completed"
    assert doc["experiment"]["prompt_template"] == "{input}"
    assert doc["experiment"]["evaluator_config"]["evaluators"][0]["id"] == "ex"
    assert [t["model"] for t in doc["experiment"]["config"]["targets"]] == [
        "m1",
        "m2",
    ]
    assert len(doc["comparison"]["targets"]) == 2
    assert "pricing" in doc["comparison"]
    third = next(r for r in results if r["case_position"] == 3)
    assert third["response_text"] == TRICKY  # exact text, no prefixing
    assert third["rendered_prompt"] == "SECRET-PROMPT-3"
    assert third["evaluations"] == [{"evaluator_id": "ex", "status": "passed"}]
    assert third["total_tokens"] == 6


async def test_json_include_responses_false_omits_text(
    session_factory: Factory,
) -> None:
    experiment_id = await seed(session_factory, [Row(1, text="hidden")])
    doc, _ = await _json(session_factory, experiment_id, include=False)
    (row,) = doc["results"]
    assert "response_text" not in row
    assert "rendered_prompt" not in row
    assert row["latency_ms"] is None
    assert row["input_tokens"] is None


async def test_json_empty_experiment_is_valid(
    session_factory: Factory,
) -> None:
    experiment_id = await seed(session_factory, [])
    doc, _ = await _json(session_factory, experiment_id)
    assert doc["results"] == []


async def test_csv_export_parses_back(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "EXPORT_BATCH_SIZE", 2)
    rows = [
        Row(
            p,
            model=m,
            latency=1.5,
            tokens=(1, 2, 3),
            evals={"ex": "passed", "has": "failed"},
            text=TRICKY,
        )
        for p in range(1, 4)
        for m in ("m1", "m2")
    ]
    experiment_id = await seed(
        session_factory,
        rows,
        models=("m1", "m2"),
        evaluators=("ex", "has"),
    )
    table = await _csv(session_factory, experiment_id, include=True)
    assert table[0] == [
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
        "eval:ex",
        "eval:has",
        "response_text",
    ]
    assert len(table) == 7  # header + 3 cases x 2 targets
    first = table[1]
    assert first[:5] == ["1", "openai", "m1", "succeeded", "1"]
    assert first[5:9] == ["1.5", "1", "2", "3"]
    assert first[9] == ""
    assert first[10] == "false"
    assert first[11:13] == ["passed", "failed"]
    assert first[13] == TRICKY
    assert all(len(r) == 14 for r in table)


async def test_csv_unknowns_are_empty_and_responses_optional(
    session_factory: Factory,
) -> None:
    experiment_id = await seed(
        session_factory,
        [Row(1, status="failed", error_kind="timeout", text="secret")],
        evaluators=("ex",),
    )
    table = await _csv(session_factory, experiment_id, include=False)
    assert "response_text" not in table[0]
    assert table[1] == [
        "1",
        "openai",
        "m1",
        "failed",
        "1",
        "",
        "",
        "",
        "",
        "timeout",
        "false",
        "",
    ]


async def test_csv_formula_cells_are_prefixed(
    session_factory: Factory,
) -> None:
    hostile = '=HYPERLINK("http://evil","x")'
    experiment_id = await seed(
        session_factory,
        [
            Row(1, text=hostile, model="=m"),
            Row(2, status="failed", error_kind="@kind", text="-1"),
            Row(3, text="\tTAB"),
            Row(4, text="\rCR"),
            Row(5, text="+1"),
        ],
        models=("=m", "m1"),
    )
    table = await _csv(session_factory, experiment_id, include=True)
    by_pos = {r[0]: r for r in table[1:]}
    assert by_pos["1"][2] == "'=m"
    assert by_pos["1"][-1] == "'" + hostile
    assert by_pos["2"][9] == "'@kind"
    assert by_pos["2"][-1] == "'-1"
    assert by_pos["3"][-1] == "'\tTAB"
    assert by_pos["4"][-1] == "'\rCR"
    assert by_pos["5"][-1] == "'+1"


async def test_running_experiment_exports_with_finished_false(
    session_factory: Factory,
) -> None:
    experiment_id = await seed(
        session_factory,
        [Row(1), Row(2, status="pending"), Row(3, status="running")],
    )
    async with session_factory() as session:
        await session.execute(
            update(Experiment)
            .where(Experiment.id == experiment_id)
            .values(status="running")
        )
        await session.commit()
    doc, _ = await _json(session_factory, experiment_id)
    assert doc["finished"] is False
    assert doc["status"] == "running"
    assert [r["status"] for r in doc["results"]] == [
        "succeeded",
        "pending",
        "running",
    ]
    table = await _csv(session_factory, experiment_id)
    assert len(table) == 4


async def test_unknown_experiment_raises(session_factory: Factory) -> None:
    with pytest.raises(ExperimentNotFoundError):
        await prepare_export(
            _scope(session_factory), ROOT, uuid.uuid4(), _metrics
        )


async def test_stream_failure_truncates_without_trailer(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_id = await seed(session_factory, [Row(1), Row(2)])
    context = await prepare_export(
        _scope(session_factory), ROOT, experiment_id, _metrics
    )

    async def boom(*args: object, **kwargs: object) -> Any:
        raise RuntimeError("db gone")
        yield  # pragma: no cover

    monkeypatch.setattr(export, "iter_rows", boom)
    chunks = await _collect(json_stream(_scope(session_factory), context, True))
    text = "".join(chunks)
    assert not text.endswith("]}")
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


async def test_prepare_export_hides_out_of_scope_experiments(
    session_factory: Factory,
) -> None:
    owner = await seed_principal(session_factory)
    experiment_id = await seed(session_factory, [Row(1)], owner=owner)
    colleague = await seed_principal(
        session_factory, Role.STAFF, owner.organization_id
    )
    admin = await seed_principal(
        session_factory, Role.ADMIN, owner.organization_id
    )
    stranger_admin = await seed_principal(session_factory, Role.ADMIN)
    for principal in (colleague, stranger_admin):
        with pytest.raises(ExperimentNotFoundError):
            await prepare_export(
                _scope(session_factory), principal, experiment_id, _metrics
            )
    for principal in (owner, admin, ROOT):
        context = await prepare_export(
            _scope(session_factory), principal, experiment_id, _metrics
        )
        assert context.experiment_id == experiment_id


async def test_truncation_flag_in_json_and_csv_with_and_without_text(
    session_factory: Factory,
) -> None:
    experiment_id = await seed(
        session_factory,
        [Row(1, text="cut", truncated=True), Row(2, text="whole")],
    )
    for include in (True, False):
        doc, _ = await _json(session_factory, experiment_id, include)
        assert [r["response_truncated"] for r in doc["results"]] == [
            True,
            False,
        ]
        table = await _csv(session_factory, experiment_id, include)
        column = table[0].index("response_truncated")
        assert [r[column] for r in table[1:]] == ["true", "false"]
