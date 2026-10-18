"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import importlib
import sys
import uuid
from collections.abc import Iterator
from types import ModuleType
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch
from sqlalchemy import select
from taskiq import InMemoryBroker

from components.evaluation.models import EvaluationResult
from components.evaluation.service import EvaluationService
from components.experiments.dependencies import build_experiment_service
from components.experiments.enums import ExperimentStatus
from components.experiments.models import Experiment
from components.experiments.runner import ExperimentRunner
from tests.access import ROOT, seed_principal
from tests.evaluation.test_repository import Factory
from tests.experiments.helpers import (
    FakeLLM,
    create_data,
    make_dataset,
    response,
)

pytestmark = pytest.mark.integration

_MODULES = (
    "taskiq_app",
    "components.evaluation.tasks",
    "components.experiments.tasks",
)


@pytest.fixture
def tasks() -> Iterator[tuple[ModuleType, ModuleType]]:
    """Import both tasks modules without a real broker connection."""
    from components.core.config import get_settings

    mp = MonkeyPatch()
    mp.setenv("RABBITMQ_URL", "amqp://u:p@localhost/")
    get_settings.cache_clear()
    for name in _MODULES:
        sys.modules.pop(name, None)
    experiments = importlib.import_module("components.experiments.tasks")
    evaluation = importlib.import_module("components.evaluation.tasks")
    yield experiments, evaluation
    for name in _MODULES:
        sys.modules.pop(name, None)
    mp.undo()
    get_settings.cache_clear()


async def test_started_experiment_ends_with_stored_evaluations(
    tasks: tuple[ModuleType, ModuleType],
    session_factory: Factory,
    monkeypatch: MonkeyPatch,
) -> None:
    experiments, evaluation = tasks
    memory = InMemoryBroker()
    evaluate: Any = memory.register_task(
        evaluation.evaluate_result.original_func,
        task_name=evaluation.EVALUATE_RESULT_TASK,
    )
    execute: Any = memory.register_task(
        experiments.execute_result.original_func,
        task_name=experiments.EXECUTE_RESULT_TASK,
    )
    monkeypatch.setattr(
        evaluation,
        "get_service",
        lambda: EvaluationService(session_factory),
    )

    async def on_succeeded(result_id: uuid.UUID) -> None:
        await evaluation.enqueue_results([result_id], task=evaluate)

    llm = FakeLLM()
    llm.handler = lambda prompt: _answer(prompt)
    runner = ExperimentRunner(
        session_factory, llm.as_service(), 2, on_succeeded=on_succeeded
    )
    monkeypatch.setattr(experiments, "get_runner", lambda: runner)
    await memory.startup()

    async def enqueue(ids: Any) -> None:
        await experiments.enqueue_results(ids, task=execute)

    owner = await seed_principal(session_factory)
    dataset_id = await make_dataset(session_factory, 3, owner)
    data = create_data(dataset_id)
    data.evaluator_config = data.evaluator_config.model_validate(
        {
            "evaluators": [
                {"id": "exact", "type": "exact_match"},
                {"id": "len", "type": "length_between", "max_length": 100},
            ]
        }
    )
    async with session_factory() as s:
        service = build_experiment_service(s, llm.as_service(), enqueue)
        experiment = await service.create(owner, data)
        await s.commit()
        experiment_id = experiment.id
    async with session_factory() as s:
        assert (
            await build_experiment_service(s, llm.as_service(), enqueue).start(
                ROOT, experiment_id
            )
            == 3
        )
    await memory.wait_all()  # generation
    await memory.wait_all()  # evaluations enqueued by the hook
    await memory.shutdown()

    async with session_factory() as s:
        rows = (await s.scalars(select(EvaluationResult))).all()
        final = await s.get(Experiment, experiment_id)
    assert final is not None
    assert final.status == ExperimentStatus.COMPLETED
    assert len(rows) == 6
    # The fake answers with each case's expected output ("out<p>").
    by_id: dict[str, set[str]] = {}
    for row in rows:
        by_id.setdefault(row.evaluator_id, set()).add(row.status)
    assert by_id == {"exact": {"passed"}, "len": {"passed"}}
    summary = await EvaluationService(session_factory).summary(
        ROOT, experiment_id
    )
    assert summary.unevaluated_results == 0


async def _answer(prompt: str) -> Any:
    # prompt is "Q: in<p>"; the case's expected output is "out<p>".
    return response("out" + prompt.removeprefix("Q: in"))
