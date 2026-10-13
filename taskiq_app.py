"""Taskiq broker and worker entry point.

Start a worker with::

    taskiq worker taskiq_app:broker --fs-discover \\
        --tasks-pattern "components/*/tasks.py"

Components register tasks by importing ``broker`` from this module in their
own ``tasks.py``; no change here is needed. Enqueuing a task only means it
was submitted: completion must be observed through persisted state.
"""

from taskiq import AsyncBroker
from taskiq_aio_pika import AioPikaBroker

from components.core.config import get_settings

WORKER_TASKS_PATTERN = "components/*/tasks.py"


def build_broker(url: str) -> AsyncBroker:
    return AioPikaBroker(url)


broker = build_broker(get_settings().rabbitmq_url.get_secret_value())
