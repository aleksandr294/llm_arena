"""Wiring of ``MetricsService``: the one place where it is constructed."""

from sqlalchemy.ext.asyncio import AsyncSession

from components.metrics.service import MetricsService


def build_metrics_service(
    session: AsyncSession, pricing_file: str | None = None
) -> MetricsService:
    return MetricsService(session, pricing_file)
