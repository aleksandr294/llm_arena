import asyncio

from fastapi import FastAPI
from httpx import AsyncClient

from api.v1.endpoints.health import get_health_checks
from components.core.config import SettingsSchema, get_settings
from components.core.health import HealthCheck


async def test_liveness_returns_200(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_unknown_api_version_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/v2/anything")

    assert response.status_code == 404


def _override(app: FastAPI, checks: dict[str, HealthCheck]) -> None:
    app.dependency_overrides[get_health_checks] = lambda: checks
    app.dependency_overrides[get_settings] = lambda: SettingsSchema(
        _env_file=None,
        database_url="postgresql+asyncpg://x",
        rabbitmq_url="amqp://x",
        health_check_timeout_seconds=0.2,
    )


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionError("postgresql://u:s3cret@db unreachable")


async def _hang() -> None:
    await asyncio.sleep(10)


async def test_ready_returns_200_when_all_healthy(
    app: FastAPI, client: AsyncClient
) -> None:
    _override(app, {"database": _ok, "rabbitmq": _ok})

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"database": "ok", "rabbitmq": "ok"}


async def test_ready_returns_503_when_database_down(
    app: FastAPI, client: AsyncClient
) -> None:
    _override(app, {"database": _down, "rabbitmq": _ok})

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"database": "failing", "rabbitmq": "ok"}
    assert "s3cret" not in response.text


async def test_ready_times_out_hanging_dependency(
    app: FastAPI, client: AsyncClient
) -> None:
    _override(app, {"database": _ok, "rabbitmq": _hang})

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["rabbitmq"] == "failing"


async def test_liveness_ignores_dependencies(
    app: FastAPI, client: AsyncClient
) -> None:
    _override(app, {"database": _down})

    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
