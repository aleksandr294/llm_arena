"""Taskiq broker and worker entry point.

Start a worker with::

    taskiq worker taskiq_app:broker --fs-discover \\
        --tasks-pattern "components/*/tasks.py"

Components register tasks by importing ``broker`` from this module in their
own ``tasks.py``; no change here is needed. Enqueuing a task only means it
was submitted: completion must be observed through persisted state.
"""

import asyncio

from prometheus_client import start_http_server
from taskiq import AsyncBroker, TaskiqEvents, TaskiqState
from taskiq_aio_pika import AioPikaBroker

from components.core.config import get_settings
from components.core.logging import configure_logging
from components.core.task_metrics import TaskMetricsMiddleware
from components.core.worker_metrics import WORKER_METRICS_REGISTRY

WORKER_TASKS_PATTERN = "components/*/tasks.py"


def build_broker(url: str) -> AsyncBroker:
    return AioPikaBroker(url)


broker = build_broker(get_settings().rabbitmq_url.get_secret_value())
# The middleware's hooks only run inside the worker's receiver loop, so
# registering it here is a no-op for the API process, which only kicks
# tasks through this same broker instance.
broker.add_middlewares(TaskMetricsMiddleware())


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _configure_worker_logging(_state: TaskiqState) -> None:
    # Runs only in the worker process, not when the API imports ``broker``.
    configure_logging(get_settings().log_level)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _start_worker_metrics_server(_state: TaskiqState) -> None:
    settings = get_settings()
    # Binds every interface *inside the container*, the same lesson
    # api/app.py documents for API_HOST: a loopback-only bind is
    # unreachable from another container (Prometheus) or from the host
    # through a published port. The host-side exposure is already
    # restricted to 127.0.0.1 by the Compose "ports:" mapping.
    start_http_server(
        settings.worker_metrics_port,
        addr="0.0.0.0",  # noqa: S104 - container-internal; see comment above
        registry=WORKER_METRICS_REGISTRY,
    )


class _BrokerState:
    """Start state of the API process's broker. The lock is created for the
    running event loop (an ``asyncio.Lock`` binds to the first loop that
    waits on it), so a second loop (tests, reload) gets its own."""

    def __init__(self) -> None:
        self.started = False
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock


_state = _BrokerState()


async def ensure_broker_started() -> None:
    """Start the broker once for processes that are not workers (the API).
    Lazy so a RabbitMQ outage does not stop the API from booting; a failed
    startup is retried on the next call."""
    async with _state.lock():
        if not _state.started:
            await broker.startup()
            _state.started = True


async def shutdown_broker() -> None:
    """Shut the broker down if ``ensure_broker_started`` started it; a no-op
    otherwise. Called when the API stops."""
    async with _state.lock():
        if _state.started:
            try:
                await broker.shutdown()
            finally:
                _state.started = False
