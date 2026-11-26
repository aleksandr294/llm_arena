import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from taskiq import InMemoryBroker
from taskiq.cli.utils import import_tasks

import components

MQ_URL = "amqp://u:p@localhost/"


@pytest.fixture
def taskiq_app(monkeypatch: MonkeyPatch) -> Iterator[object]:
    from components.core.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("RABBITMQ_URL", MQ_URL)
    get_settings.cache_clear()
    sys.modules.pop("taskiq_app", None)
    module = importlib.import_module("taskiq_app")
    yield module
    sys.modules.pop("taskiq_app", None)
    get_settings.cache_clear()


def test_broker_is_built_from_settings(taskiq_app: object) -> None:
    from taskiq_aio_pika import AioPikaBroker

    broker = taskiq_app.broker  # type: ignore[attr-defined]
    assert isinstance(broker, AioPikaBroker)


async def test_task_is_registered_and_executes() -> None:
    broker = InMemoryBroker()

    @broker.task
    async def double(value: int) -> int:
        return value * 2

    await broker.startup()
    submitted = await double.kiq(21)
    result = await submitted.wait_result(timeout=2)
    await broker.shutdown()

    assert result.return_value == 42
    assert not result.is_err


def test_component_tasks_discovered_without_touching_broker(
    taskiq_app: object, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    pattern = taskiq_app.WORKER_TASKS_PATTERN  # type: ignore[attr-defined]
    package = tmp_path / "components" / "probe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "tasks.py").write_text(
        "from taskiq_app import broker\n\n\n"
        "@broker.task(task_name='probe.ping')\n"
        "async def ping() -> str:\n    return 'pong'\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        components,
        "__path__",
        [*components.__path__, str(tmp_path / "components")],
    )
    monkeypatch.syspath_prepend(str(Path(__file__).parent.parent))

    import_tasks([], pattern, True)

    broker = taskiq_app.broker  # type: ignore[attr-defined]
    assert broker.find_task("probe.ping") is not None
    sys.modules.pop("components.probe.tasks", None)
    sys.modules.pop("components.probe", None)


class _FakeBroker:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def startup(self) -> None:
        self.events.append("startup")

    async def shutdown(self) -> None:
        self.events.append("shutdown")


async def test_shutdown_after_a_publish_and_noop_when_never_started(
    taskiq_app: object, monkeypatch: MonkeyPatch
) -> None:
    module = taskiq_app
    fake = _FakeBroker()
    monkeypatch.setattr(module, "broker", fake)
    await module.shutdown_broker()  # type: ignore[attr-defined]
    assert fake.events == []
    await module.ensure_broker_started()  # type: ignore[attr-defined]
    await module.ensure_broker_started()  # type: ignore[attr-defined]
    await module.shutdown_broker()  # type: ignore[attr-defined]
    await module.shutdown_broker()  # type: ignore[attr-defined]
    assert fake.events == ["startup", "shutdown"]


def test_broker_lock_is_not_shared_between_event_loops(
    taskiq_app: object, monkeypatch: MonkeyPatch
) -> None:
    import asyncio

    module = taskiq_app
    fake = _FakeBroker()
    monkeypatch.setattr(module, "broker", fake)

    async def start_and_stop() -> None:
        await module.ensure_broker_started()  # type: ignore[attr-defined]
        await module.shutdown_broker()  # type: ignore[attr-defined]

    asyncio.run(start_and_stop())
    asyncio.run(start_and_stop())
    assert fake.events == ["startup", "shutdown"] * 2
