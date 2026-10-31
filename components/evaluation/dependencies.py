"""Wiring of ``EvaluationService``: the one place where it is constructed."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.core.database import SessionScope
from components.evaluation.service import EvaluationService
from components.third_party.llm.service import LLMService


def build_evaluation_service(
    session: AsyncSession | None = None,
    *,
    scope: SessionScope | None = None,
    llm_service: LLMService | None = None,
) -> EvaluationService:
    return EvaluationService(session, scope=scope, llm_service=llm_service)
