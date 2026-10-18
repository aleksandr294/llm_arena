"""Test helper: calls ``EvaluationService`` the way an entry point does."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from components.core.database import session_scope
from components.core.schemas import Principal
from components.evaluation.dependencies import build_evaluation_service
from components.evaluation.schemas import (
    EvaluationResultSchema,
    EvaluationSummarySchema,
)


class ScopedEvaluation:
    """Each call opens a ``session_scope`` on the test session factory (one
    transaction, one commit), builds the service on it and delegates."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def evaluate_result(self, result_id: uuid.UUID) -> int:
        async with session_scope(self._factory) as session:
            return await build_evaluation_service(session).evaluate_result(
                result_id
            )

    async def backfill(
        self,
        principal: Principal,
        experiment_id: uuid.UUID,
        limit: int | None = None,
    ) -> tuple[list[uuid.UUID], int]:
        async with session_scope(self._factory) as session:
            return await build_evaluation_service(session).backfill(
                principal, experiment_id, limit
            )

    async def list_evaluations(
        self, *args: Any, **kwargs: Any
    ) -> tuple[list[EvaluationResultSchema], int]:
        async with session_scope(self._factory) as session:
            return await build_evaluation_service(session).list_evaluations(
                *args, **kwargs
            )

    async def summary(
        self, principal: Principal, experiment_id: uuid.UUID
    ) -> EvaluationSummarySchema:
        async with session_scope(self._factory) as session:
            return await build_evaluation_service(session).summary(
                principal, experiment_id
            )
