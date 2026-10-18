"""Wiring of ``ExperimentService``: the one place where it is constructed."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.experiments.service import EnqueueResults, ExperimentService
from components.third_party.llm.service import LLMService


def build_experiment_service(
    session: AsyncSession,
    llm_service: LLMService,
    enqueue: EnqueueResults,
) -> ExperimentService:
    return ExperimentService(session, llm_service, enqueue)
