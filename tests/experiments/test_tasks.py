"""Integration tests: need PostgreSQL (``DATABASE_URL``)."""

import importlib
import sys
from collections.abc import Iterator
from types import ModuleType
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch
from taskiq import InMemoryBroker

from components.experiments.models import ExperimentStatus, ResultStatus
from components.experiments.runner import ExperimentRunner
from components.experiments.service import ExperimentService
from tests.experiments.helpers import (
    Factory,
    FakeLLM,
    make_experiment,
)
from tests.experiments.test_runner import _experiment, _ids, _row

pytestmark = pytest.mark.integration


@pytest.fixture
def tasks_module(monkeypatch: MonkeyPatch) -> Iterator[ModuleType]:
    """Import the tasks module without a real broker connection (building
    the AioPika broker does not connect)."""
    from components.core.config import get_settings

    monkeypatch.setenv("RABBITMQ_URL", "amqp://u:p@localhost/")
    get_settings.cache_clear()
    sys.modules.pop("taskiq_app", None)
    sys.modules.pop("components.experiments.tasks", None)
    module = importlib.import_module("components.experiments.tasks")
    yield module
    sys.modules.pop("components.experiments.tasks", None)
    sys.modules.pop("taskiq_app", None)
    get_settings.cache_clear()


async def test_started_experiment_executes_to_final_status(
    tasks_module: ModuleType,
    session_factory: Factory,
    monkeypatch: MonkeyPatch,
) -> None:
    llm = FakeLLM()
    runner = ExperimentRunner(session_factory, llm.as_service(), 2)
    monkeypatch.setattr(tasks_module, "get_runner", lambda: runner)
    memory = InMemoryBroker()
    task: Any = memory.register_task(
        tasks_module.execute_result.original_func,
        task_name=tasks_module.EXECUTE_RESULT_TASK,
    )
    await memory.startup()

    async def enqueue(ids: Any) -> None:
        await tasks_module.enqueue_results(ids, task=task)

    experiment_id = await make_experiment(session_factory, cases=3)
    async with session_factory() as s:
        service = ExperimentService(s, llm.as_service(), enqueue)
        assert await service.start(experiment_id) == 3
    await memory.wait_all()
    await memory.shutdown()

    assert len(llm.calls) == 3
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.COMPLETED
    ids = await _ids(session_factory, experiment_id, ResultStatus.SUCCEEDED)
    assert len(ids) == 3
    assert (await _row(session_factory, ids[0])).attempts == 1


async def test_enqueueing_alone_leaves_results_pending(
    tasks_module: ModuleType, session_factory: Factory
) -> None:
    llm = FakeLLM()
    # No worker consumes the queue: a recorder replaces the task's kiq.
    sent: list[str] = []

    class Recorder:
        async def kiq(self, result_id: str) -> None:
            sent.append(result_id)

    async def enqueue(ids: Any) -> None:
        await tasks_module.enqueue_results(ids, task=Recorder())

    experiment_id = await make_experiment(session_factory, cases=2)
    async with session_factory() as s:
        service = ExperimentService(s, llm.as_service(), enqueue)
        await service.start(experiment_id)

    assert len(sent) == 2
    assert llm.calls == []
    experiment = await _experiment(session_factory, experiment_id)
    assert experiment.status == ExperimentStatus.RUNNING
    pending = await _ids(session_factory, experiment_id, ResultStatus.PENDING)
    assert sorted(str(i) for i in pending) == sorted(sent)


async def test_submit_results_starts_broker_once(
    tasks_module: ModuleType, monkeypatch: MonkeyPatch
) -> None:
    """The API process starts the broker lazily, once, then enqueues."""
    import uuid

    started: list[int] = []
    sent: list[Any] = []

    async def startup() -> None:
        started.append(1)

    async def fake_enqueue(ids: Any) -> None:
        sent.extend(ids)

    monkeypatch.setattr(tasks_module.broker, "startup", startup)
    monkeypatch.setattr(tasks_module, "enqueue_results", fake_enqueue)
    first, second = uuid.uuid4(), uuid.uuid4()
    await tasks_module.submit_results([first])
    await tasks_module.submit_results([second])
    assert started == [1]
    assert sent == [first, second]
