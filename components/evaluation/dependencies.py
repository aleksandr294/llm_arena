"""Wiring of ``EvaluationService``: the one place where it is constructed."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.evaluation.service import EvaluationService


def build_evaluation_service(session: AsyncSession) -> EvaluationService:
    return EvaluationService(session)
