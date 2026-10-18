"""Thin Taskiq wrapper around ``EvaluationService.evaluate_result``.

Like the experiment task it carries only ``result_id``; the stored
configuration decides what runs. Enqueueing only means submitted.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from components.core.database import session_scope
from components.evaluation.dependencies import build_evaluation_service
from taskiq_app import broker, ensure_broker_started

EVALUATE_RESULT_TASK = "evaluation.evaluate_result"


@broker.task(task_name=EVALUATE_RESULT_TASK)
async def evaluate_result(result_id: str) -> None:
    # The entry point owns the session: one read, pure evaluation and one
    # insert in a single transaction, committed by the scope.
    async with session_scope() as session:
        await build_evaluation_service(session).evaluate_result(
            uuid.UUID(result_id)
        )


async def enqueue_results(
    result_ids: Sequence[uuid.UUID], task: Any = evaluate_result
) -> None:
    """Submit one task per result id. ``task`` is injectable for tests."""
    for result_id in result_ids:
        await task.kiq(str(result_id))


async def submit_results(result_ids: Sequence[uuid.UUID]) -> None:
    """``enqueue_results`` for the API process (starts the broker lazily)."""
    await ensure_broker_started()
    await enqueue_results(result_ids)
