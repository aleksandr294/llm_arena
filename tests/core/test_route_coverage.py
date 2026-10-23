"""Safety net: a route added without authentication fails this test."""

import re
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator

from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from starlette.routing import BaseRoute

from api.v1.utils import get_settings_dependency
from components.core.config import SettingsSchema
from components.core.database import get_session
from conftest import TEST_JWT_SECRET

# Routes that are public on purpose. Anything else must answer 401 without a
# token. Adding an entry here is a deliberate decision to expose a route.
PUBLIC: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/health/live"),
        ("GET", "/api/v1/health/ready"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/refresh"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/auth/validate"),
    }
)
# Framework routes (not APIRoute instances) that serve documentation.
FRAMEWORK_PATHS = frozenset(
    {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
)


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)


def _flatten(
    routes: Iterable[BaseRoute], prefix: str = ""
) -> Iterator[tuple[str, BaseRoute]]:
    """Every route with its full path. FastAPI includes routers lazily:
    ``app.routes`` holds wrappers (``original_router`` plus the include
    prefix) instead of the routes themselves."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _flatten(
                inner.routes, prefix + route.include_context.prefix  # type: ignore[attr-defined]
            )
        else:
            yield prefix + getattr(route, "path", ""), route


def _api_routes(app: FastAPI) -> list[tuple[str, APIRoute]]:
    return [
        (path, r) for path, r in _flatten(app.routes) if isinstance(r, APIRoute)
    ]


def _others(app: FastAPI) -> list[str]:
    return [
        path
        for path, r in _flatten(app.routes)
        if not isinstance(r, APIRoute) and path not in FRAMEWORK_PATHS
    ]


async def _no_session() -> AsyncIterator[None]:
    # A request without a token is refused before any database access.
    yield None


async def test_every_route_requires_a_token_unless_public(
    app: FastAPI,
) -> None:
    app.dependency_overrides[get_session] = _no_session
    app.dependency_overrides[get_settings_dependency] = lambda: SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
        jwt_secret_key=TEST_JWT_SECRET,
    )
    unknown = _others(app)
    assert unknown == [], f"unexpected non-API routes: {unknown}"

    checked: list[tuple[str, str]] = []
    failures: list[str] = []
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path, route in _api_routes(app):
            for method in sorted(
                (route.methods or set()) - {"HEAD", "OPTIONS"}
            ):
                if (method, path) in PUBLIC:
                    continue
                checked.append((method, path))
                response = await client.request(method, _concrete(path))
                if response.status_code != 401:
                    failures.append(
                        f"{method} {path} -> {response.status_code}"
                    )
    assert failures == [], f"routes answering without a token: {failures}"
    # Guard against the loop silently checking nothing.
    assert ("GET", "/api/v1/datasets") in checked
    assert ("GET", "/api/v1/experiments/{experiment_id}/export") in checked


def test_public_allowlist_has_no_stale_entries(app: FastAPI) -> None:
    registered = {
        (method, path)
        for path, route in _api_routes(app)
        for method in route.methods or ()
    }
    assert registered >= PUBLIC


def test_route_walk_matches_the_openapi_document(app: FastAPI) -> None:
    """Cross-check of the walk above (which uses FastAPI internals): every
    documented operation must have been found, so a FastAPI change that
    breaks the walk fails here instead of silently checking nothing."""
    walked = {
        (method, path)
        for path, route in _api_routes(app)
        for method in route.methods or ()
    }
    documented = {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
    }
    assert documented <= walked
    assert len(documented) > 20
