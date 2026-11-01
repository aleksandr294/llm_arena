"""HTTP metrics middleware and the /metrics endpoint."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    UNMATCHED_ROUTE,
    MetricsMiddleware,
    metrics_endpoint,
)
from components.core.config import SettingsSchema
from conftest import TEST_JWT_SECRET

pytestmark = pytest.mark.filterwarnings("ignore")


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/plain")
    async def plain() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/boom")
    async def boom() -> dict[str, bool]:
        raise RuntimeError("boom")

    app.add_middleware(MetricsMiddleware)
    return app


def _counter_value(route: str, status: str) -> float:
    value: float = HTTP_REQUESTS_TOTAL.labels(
        route=route, status=status
    )._value.get()
    return value


def _observation_count(route: str) -> float:
    # Histogram children expose no cumulative-count accessor; summing
    # every per-bucket value (each observation lands in exactly one) does.
    child = HTTP_REQUEST_DURATION_SECONDS.labels(route=route)
    return float(sum(bucket.get() for bucket in child._buckets))


async def test_successful_request_is_counted() -> None:
    app = _probe_app()
    before = _counter_value("/plain", "200")
    before_latency = _observation_count("/plain")
    async with _client(app) as client:
        response = await client.get("/plain")
    assert response.status_code == 200
    assert _counter_value("/plain", "200") == before + 1
    assert _observation_count("/plain") == before_latency + 1


async def test_unhandled_exception_counts_as_500() -> None:
    app = _probe_app()
    before = _counter_value("/boom", "500")
    async with _client(app) as client:
        # Starlette's ServerErrorMiddleware sends the 500 response, then
        # re-raises for the test to see (httpx's ASGITransport propagates
        # it); our middleware already recorded the metric by then.
        with pytest.raises(RuntimeError, match="boom"):
            await client.get("/boom")
    assert _counter_value("/boom", "500") == before + 1


async def test_unmatched_route_does_not_leak_raw_path() -> None:
    app = _probe_app()
    before = _counter_value(UNMATCHED_ROUTE, "404")
    async with _client(app) as client:
        response = await client.get("/no-such-path-at-all")
    assert response.status_code == 404
    assert _counter_value(UNMATCHED_ROUTE, "404") == before + 1


async def test_metrics_endpoint_serves_registered_counters() -> None:
    from api.app import create_app

    settings = SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
        jwt_secret_key=TEST_JWT_SECRET,
        admin_secret_key=None,
        _env_file=None,
    )
    app = create_app(settings)
    async with _client(app) as client:
        await client.get("/api/v1/health/live")
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "http_requests_total" in response.text
    assert "http_request_duration_seconds" in response.text


def test_metrics_endpoint_is_callable_directly() -> None:
    import asyncio

    response = asyncio.run(metrics_endpoint())
    assert response.status_code == 200
