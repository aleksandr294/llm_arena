"""Thin Taskiq wrapper around ``ExperimentRunner``.

The task carries only ``result_id``; everything else is read from the
database, so a redelivered or stale message cannot change what is executed.
Enqueueing only means the message was submitted, never that the result ran.
"""

import asyncio
import uuid
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from components.core.config import get_settings
from components.core.database import get_session_factory
from components.experiments.runner import ExperimentRunner
from components.third_party.llm.service import LLMService
from taskiq_app import broker

EXECUTE_RESULT_TASK = "experiments.execute_result"


@lru_cache
def get_runner() -> ExperimentRunner:
    """One runner per process so the per-provider semaphores are shared by
    all concurrently running tasks."""
    settings = get_settings()
    return ExperimentRunner(
        get_session_factory(),
        LLMService(settings),
        settings.llm_max_concurrency,
    )


@broker.task(task_name=EXECUTE_RESULT_TASK)
async def execute_result(result_id: str) -> None:
    await get_runner().run(uuid.UUID(result_id))


async def enqueue_results(
    result_ids: Sequence[uuid.UUID], task: Any = execute_result
) -> None:
    """Submit one task per result id. ``task`` is injectable for tests."""
    for result_id in result_ids:
        await task.kiq(str(result_id))


_broker_lock = asyncio.Lock()
_broker_started = False


async def submit_results(result_ids: Sequence[uuid.UUID]) -> None:
    """``enqueue_results`` for the API process, which (unlike a worker)
    does not start the broker itself. Started lazily on first use so a
    RabbitMQ outage does not stop the API from booting; a failed startup
    is retried on the next call."""
    global _broker_started
    async with _broker_lock:
        if not _broker_started:
            await broker.startup()
            _broker_started = True
    await enqueue_results(result_ids)
