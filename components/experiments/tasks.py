"""Thin Taskiq wrapper around ``ExperimentRunner``.

The task carries only ``result_id``; everything else is read from the
database, so a redelivered or stale message cannot change what is executed.
Enqueueing only means the message was submitted, never that the result ran.
"""

import uuid
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from components.core.config import get_settings
from components.core.database import get_session_factory
from components.evaluation import tasks as evaluation_tasks
from components.experiments.runner import ExperimentRunner
from components.third_party.llm.service import LLMService
from taskiq_app import broker, ensure_broker_started

EXECUTE_RESULT_TASK = "experiments.execute_result"


async def _enqueue_evaluation(result_id: uuid.UUID) -> None:
    await evaluation_tasks.enqueue_results([result_id])


@lru_cache
def get_runner() -> ExperimentRunner:
    """One runner per process so the per-provider semaphores are shared by
    all concurrently running tasks."""
    settings = get_settings()
    return ExperimentRunner(
        get_session_factory(),
        LLMService(settings),
        settings.llm_max_concurrency,
        on_succeeded=_enqueue_evaluation,
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


async def submit_results(result_ids: Sequence[uuid.UUID]) -> None:
    """``enqueue_results`` for the API process, which (unlike a worker)
    does not start the broker itself."""
    await ensure_broker_started()
    await enqueue_results(result_ids)
