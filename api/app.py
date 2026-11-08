from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from admin import mount_admin
from api.metrics import MetricsMiddleware, metrics_endpoint
from api.middleware import SecurityMiddleware
from api.v1.router import api_v1_router
from components.core.config import SettingsSchema, get_settings
from components.core.database import dispose_engine
from components.third_party.llm.service import LLMService
from taskiq_app import shutdown_broker

API_VERSION = "0.1.0"

API_DESCRIPTION = """\
Backend for evaluating and comparing LLM providers and models: datasets of \
cases, experiments run against several models, evaluations of the \
responses, and metrics for comparing quality, latency, tokens, cost and \
errors.

## Authentication

1. `POST /api/v1/auth/login` with email and password returns a short-lived \
access token and a refresh token.
2. Send the access token as `Authorization: Bearer <access_token>` on every \
other request (use the Authorize button here).
3. When it expires, `POST /api/v1/auth/refresh` rotates the refresh token \
and returns a new pair. `POST /api/v1/auth/logout` revokes a refresh \
token.

Only the health and authentication endpoints are public. The caller is \
re-read from the database on every request, so role changes, deactivation \
and organization blocking apply immediately.

## Roles and visibility

- **staff**: sees and manages only their own datasets and experiments.
- **admin**: sees every dataset and experiment of their organization and \
the users of that organization.
- **superadmin**: sees everything, across organizations, and manages \
organizations.

A resource outside the caller's visibility answers `404`, as if it did not \
exist. Errors have the body `{"detail": ...}`; `422` carries a list of \
validation problems. A request body over the configured limit (2 MiB by \
default) answers `413`.
"""

OPENAPI_TAGS = [
    {
        "name": "authentication",
        "description": "Sign in, refresh and revoke tokens, validate a token.",
    },
    {
        "name": "organizations",
        "description": "Create, list, update and block organizations "
        "(superadmin only).",
    },
    {
        "name": "users",
        "description": "Create and manage users within the caller's "
        "visibility.",
    },
    {
        "name": "datasets",
        "description": "Datasets of evaluation cases and their test cases.",
    },
    {
        "name": "experiments",
        "description": "Configure experiments, start, cancel and retry them, "
        "and read their results.",
    },
    {
        "name": "evaluations",
        "description": "Evaluate stored responses and read evaluation "
        "outcomes.",
    },
    {
        "name": "metrics",
        "description": "Quality, latency, token and cost comparison of an "
        "experiment's models, and JSON or CSV export.",
    },
    {
        "name": "health",
        "description": "Liveness and readiness probes (public).",
    },
]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Release what the process holds so a stop leaves no open connections.
    # Nested so a failing step (for example the broker while RabbitMQ is
    # down) does not skip the steps after it.
    try:
        await shutdown_broker()
    finally:
        try:
            await app.state.llm_service.aclose()
        finally:
            await dispose_engine()


def create_app(settings: SettingsSchema | None = None) -> FastAPI:
    """Build the application. Invalid settings (for example a weak admin
    secret) fail here, at startup."""
    settings = settings if settings is not None else get_settings()
    docs = settings.api_docs_enabled
    app = FastAPI(
        title="LLM Arena",
        version=API_VERSION,
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        swagger_ui_parameters={"persistAuthorization": True},
        lifespan=_lifespan,
    )
    # One service (and its provider HTTP clients) for the process lifetime;
    # closed in the lifespan shutdown.
    app.state.llm_service = LLMService(settings)
    app.include_router(api_v1_router)
    # Mounted last so it never shadows an API route; a no-op without
    # ADMIN_SECRET_KEY.
    mount_admin(app, settings)
    # Added after the mount so it also wraps the panel.
    app.add_middleware(
        SecurityMiddleware,
        max_body_bytes=settings.max_request_body_bytes,
        hsts_max_age_seconds=settings.hsts_max_age_seconds,
    )
    # Outermost: counts every response, including one SecurityMiddleware
    # answers itself (413, generic 500), before any header decoration.
    app.add_middleware(MetricsMiddleware)
    app.add_api_route(
        "/metrics",
        metrics_endpoint,
        methods=["GET"],
        include_in_schema=False,
    )
    return app
