import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from components.core.config import SettingsSchema, get_settings
from components.core.health import (
    HealthCheck,
    check_database,
    check_rabbitmq,
    run_check,
)

router = APIRouter(prefix="/health", tags=["health"])


def get_health_checks() -> dict[str, HealthCheck]:
    return {"database": check_database, "rabbitmq": check_rabbitmq}


@router.get("/live")
async def live() -> dict[str, str]:
    """Liveness: process is up. Never touches external dependencies."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    response: Response,
    settings: Annotated[SettingsSchema, Depends(get_settings)],
    checks: Annotated[dict[str, HealthCheck], Depends(get_health_checks)],
) -> dict[str, str]:
    """Readiness: every dependency answers within the timeout."""
    timeout_seconds = settings.health_check_timeout_seconds
    statuses = await asyncio.gather(
        *(run_check(check, timeout_seconds) for check in checks.values())
    )
    result = dict(zip(checks, statuses, strict=True))
    if "failing" in statuses:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
