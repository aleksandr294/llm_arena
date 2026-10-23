"""The generated OpenAPI document and the documentation switch."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.app import create_app
from components.core.config import SettingsSchema
from conftest import TEST_JWT_SECRET

PUBLIC: frozenset[tuple[str, str]] = frozenset(
    {
        ("get", "/api/v1/health/live"),
        ("get", "/api/v1/health/ready"),
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/auth/refresh"),
        ("post", "/api/v1/auth/logout"),
        ("post", "/api/v1/auth/validate"),
    }
)
ERROR_REF = "#/components/schemas/ErrorSchema"
VALIDATION_REF = "#/components/schemas/HTTPValidationError"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _settings(**overrides: Any) -> SettingsSchema:
    return SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
        jwt_secret_key=TEST_JWT_SECRET,
        admin_secret_key=None,
        _env_file=None,
        **overrides,
    )


def _operations(app: FastAPI) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (method, path): op
        for path, item in app.openapi()["paths"].items()
        for method, op in item.items()
        if method in HTTP_METHODS
    }


def _ref(response: dict[str, Any]) -> str:
    return str(response["content"]["application/json"]["schema"]["$ref"])


def test_every_operation_has_a_tag_and_a_summary(app: FastAPI) -> None:
    operations = _operations(app)
    assert len(operations) > 20
    for key, op in operations.items():
        assert op.get("tags"), key
        assert op.get("summary"), key
        # A summary must be written, not FastAPI's name-derived default.
        assert op["summary"] != op["operationId"], key


def test_metadata_and_tag_descriptions(app: FastAPI) -> None:
    schema = app.openapi()
    assert schema["info"]["title"] == "LLM Arena"
    assert schema["info"]["version"]
    description = schema["info"]["description"]
    for word in ("Authentication", "Bearer", "staff", "admin", "superadmin"):
        assert word in description
    declared = {t["name"]: t["description"] for t in schema["tags"]}
    assert set(declared) == {
        "authentication",
        "organizations",
        "users",
        "datasets",
        "experiments",
        "evaluations",
        "metrics",
        "health",
    }
    assert all(declared.values())
    used = {t for op in _operations(app).values() for t in op["tags"]}
    assert used == set(declared)


def test_bearer_scheme_is_declared_and_only_public_routes_skip_it(
    app: FastAPI,
) -> None:
    schema = app.openapi()
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    operations = _operations(app)
    assert operations.keys() >= PUBLIC
    for key, op in operations.items():
        if key in PUBLIC:
            assert not op.get("security"), key
        else:
            assert op.get("security") == [{"HTTPBearer": []}], key


def test_error_responses_per_rule(app: FastAPI) -> None:
    for (method, path), op in _operations(app).items():
        responses = op["responses"]
        key = (method, path)
        if key not in PUBLIC:
            assert "401" in responses, key
        if path.startswith("/api/v1/organizations"):
            assert "403" in responses, key
        if "{" in path and key not in PUBLIC:
            assert "404" in responses, key
        for code, response in responses.items():
            if code in {"401", "403", "404", "409", "503"}:
                assert _ref(response) == ERROR_REF, (key, code)
        has_input = bool(op.get("parameters") or op.get("requestBody"))
        if has_input:
            assert _ref(responses["422"]) == VALIDATION_REF, key
    experiment_actions = [
        "/api/v1/experiments/{experiment_id}/start",
        "/api/v1/experiments/{experiment_id}/cancel",
        "/api/v1/experiments/{experiment_id}/retry-failed",
    ]
    operations = _operations(app)
    for path in experiment_actions:
        assert {"401", "404", "409"} <= operations[("post", path)][
            "responses"
        ].keys()
    assert "409" in operations[("post", "/api/v1/users")]["responses"]
    assert "409" in operations[("post", "/api/v1/organizations")]["responses"]
    assert "403" in operations[("post", "/api/v1/users")]["responses"]


def test_error_schema_matches_the_runtime_shape(app: FastAPI) -> None:
    schemas = app.openapi()["components"]["schemas"]
    error = schemas["ErrorSchema"]
    assert list(error["properties"]) == ["detail"]
    assert error["properties"]["detail"]["type"] == "string"
    assert error["required"] == ["detail"]


async def test_admin_panel_routes_are_not_documented() -> None:
    # The panel itself is covered by the admin tests; here we only check
    # that nothing in the schema starts with the admin prefix.
    app = create_app(_settings())
    assert not [p for p in app.openapi()["paths"] if p.startswith("/admin")]


async def _status(app: FastAPI, path: str) -> int:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return (await client.get(path)).status_code


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_are_served_by_default(path: str) -> None:
    assert await _status(create_app(_settings()), path) == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_can_be_turned_off(path: str) -> None:
    app = create_app(_settings(api_docs_enabled=False))
    assert await _status(app, path) == 404
    assert await _status(app, "/api/v1/health/live") == 200


async def test_swagger_ui_keeps_the_authorization() -> None:
    app = create_app(_settings())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = (await client.get("/docs")).text
    assert '"persistAuthorization": true' in page
