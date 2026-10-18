"""Auth API tests: need PostgreSQL (``DATABASE_URL``)."""

import base64
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.utils import get_principal, get_settings_dependency
from components.auth.models import RefreshToken
from components.auth.security import (
    create_access_token,
    create_refresh_token,
    hash_token_id,
)
from components.core.config import SettingsSchema
from components.core.database import get_session, session_scope
from components.core.enums import Role
from components.core.schemas import Principal
from components.organizations.models import Organization
from components.users.models import User
from tests.auth.conftest import PASSWORD, Factory, Seeded, seed_user

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _overrides(
    app: FastAPI, session_factory: Factory, settings: SettingsSchema
) -> None:
    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_scope(session_factory) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings_dependency] = lambda: settings

    @app.get("/whoami")
    async def whoami(
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> dict[str, str | None]:
        return {
            "user_id": str(principal.user_id),
            "organization_id": (
                str(principal.organization_id)
                if principal.organization_id
                else None
            ),
            "role": principal.role.value,
        }


async def _login(client: AsyncClient, user: Seeded) -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- login -----------------------------------------------------------------


async def test_login_200(client: AsyncClient, session_factory: Factory) -> None:
    user = await seed_user(session_factory)
    body = await _login(client, user)
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["access_token"] and body["refresh_token"]


async def test_login_failures_have_identical_body(
    client: AsyncClient, session_factory: Factory
) -> None:
    await seed_user(session_factory, "ok@example.com")
    await seed_user(session_factory, "off@example.com", is_active=False)
    await seed_user(session_factory, "org@example.com", org_active=False)
    responses = []
    for email, password in [
        ("ok@example.com", "wrong password!!"),
        ("nobody@example.com", PASSWORD),
        ("off@example.com", PASSWORD),
        ("org@example.com", PASSWORD),
    ]:
        responses.append(
            await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": password},
            )
        )
    assert {r.status_code for r in responses} == {401}
    assert len({r.text for r in responses}) == 1
    assert "access_token" not in responses[0].text
    assert responses[0].headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": "a@example.com"},
        {"password": PASSWORD},
        {"email": "", "password": PASSWORD},
        {"email": "a@example.com", "password": ""},
        {"email": "a@example.com", "password": "x" * 129},
        {"email": 5, "password": PASSWORD},
    ],
)
async def test_login_422(
    client: AsyncClient, payload: dict[str, object]
) -> None:
    response = await client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 422


# --- refresh / logout ------------------------------------------------------


async def test_refresh_and_reuse(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    first = await _login(client, user)
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]

    reuse = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert reuse.status_code == 401
    assert reuse.headers["www-authenticate"] == "Bearer"
    newer = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert newer.status_code == 401
    assert reuse.json() == newer.json()


async def test_refresh_invalid_and_422(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "garbage"}
    )
    assert response.status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={})
    ).status_code == 422


async def test_refresh_expired(
    client: AsyncClient, session_factory: Factory, settings: SettingsSchema
) -> None:
    user = await seed_user(session_factory)
    old = create_refresh_token(
        settings,
        user.user_id,
        uuid.uuid4(),
        now=datetime.now(UTC) - timedelta(days=30),
    )
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old.token}
    )
    assert response.status_code == 401


async def test_logout_then_refresh_fails(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    pair = await _login(client, user)
    body = {"refresh_token": pair["refresh_token"]}
    assert (
        await client.post("/api/v1/auth/logout", json=body)
    ).status_code == 204
    assert (
        await client.post("/api/v1/auth/logout", json=body)
    ).status_code == 204
    assert (
        await client.post("/api/v1/auth/refresh", json=body)
    ).status_code == 401


async def test_logout_invalid_and_422(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": "garbage"}
    )
    assert response.status_code == 401
    assert (
        await client.post("/api/v1/auth/logout", json={})
    ).status_code == 422


async def test_logout_expired_token_is_204_and_ends_login(
    client: AsyncClient, session_factory: Factory, settings: SettingsSchema
) -> None:
    user = await seed_user(session_factory)
    pair = await _login(client, user)
    async with session_factory() as s:
        family_id = (await s.scalars(select(RefreshToken))).one().family_id
    expired = create_refresh_token(
        settings,
        user.user_id,
        family_id,
        now=datetime.now(UTC) - timedelta(days=30),
    )
    async with session_factory() as s:
        await s.execute(
            update(RefreshToken).values(token_hash=hash_token_id(expired.jti))
        )
        await s.commit()
    response = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": expired.token}
    )
    assert response.status_code == 204
    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert refreshed.status_code == 401


async def test_login_email_with_nul_is_422(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "a\u0000@example.com", "password": "whatever-long"},
    )
    assert response.status_code == 422


async def test_deactivation_ends_refresh(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    pair = await _login(client, user)
    async with session_factory() as s:
        await s.execute(
            update(User).where(User.id == user.user_id).values(is_active=False)
        )
        await s.commit()
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert response.status_code == 401


# --- validate --------------------------------------------------------------


async def test_validate_valid(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory, role=Role.ADMIN)
    pair = await _login(client, user)
    response = await client.post(
        "/api/v1/auth/validate", json={"token": pair["access_token"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["user_id"] == str(user.user_id)
    assert body["organization_id"] == str(user.organization_id)
    assert body["role"] == "admin"
    assert body["expires_at"] and body["reason"] is None


async def test_validate_reasons(
    client: AsyncClient, session_factory: Factory, settings: SettingsSchema
) -> None:
    user = await seed_user(session_factory)
    expired = create_access_token(
        settings, user.user_id, now=datetime.now(UTC) - timedelta(days=1)
    )
    cases = {
        expired.token: "expired",
        "garbage": "invalid",
        (await _login(client, user))["refresh_token"]: "invalid",
    }
    for token, reason in cases.items():
        response = await client.post(
            "/api/v1/auth/validate", json={"token": token}
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False
        assert response.json()["reason"] == reason


async def test_validate_deactivated_user(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    token = (await _login(client, user))["access_token"]
    async with session_factory() as s:
        await s.execute(
            update(User).where(User.id == user.user_id).values(is_active=False)
        )
        await s.commit()
    response = await client.post("/api/v1/auth/validate", json={"token": token})
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["reason"] == "revoked"


async def test_validate_422(client: AsyncClient) -> None:
    assert (
        await client.post("/api/v1/auth/validate", json={})
    ).status_code == 422


# --- get_principal ---------------------------------------------------------


async def test_principal_from_database(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory, role=Role.STAFF)
    token = (await _login(client, user))["access_token"]
    response = await client.get("/whoami", headers=_bearer(str(token)))
    assert response.status_code == 200
    assert response.json()["role"] == "staff"
    assert response.json()["user_id"] == str(user.user_id)
    async with session_factory() as s:  # role change applies at once
        await s.execute(
            update(User).where(User.id == user.user_id).values(role="admin")
        )
        await s.commit()
    response = await client.get("/whoami", headers=_bearer(str(token)))
    assert response.json()["role"] == "admin"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "token abc"},
        {"Authorization": "Bearer not-a-jwt"},
    ],
)
async def test_bearer_parsing_errors_are_401(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/whoami", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_deactivated_user_token_stops_immediately(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    token = str((await _login(client, user))["access_token"])
    assert (
        await client.get("/whoami", headers=_bearer(token))
    ).status_code == 200
    async with session_factory() as s:
        await s.execute(
            update(User).where(User.id == user.user_id).values(is_active=False)
        )
        await s.commit()
    response = await client.get("/whoami", headers=_bearer(token))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_deactivated_organization_token_stops(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    token = str((await _login(client, user))["access_token"])
    async with session_factory() as s:
        await s.execute(
            update(Organization)
            .where(Organization.id == user.organization_id)
            .values(is_active=False)
        )
        await s.commit()
    assert (
        await client.get("/whoami", headers=_bearer(token))
    ).status_code == 401


async def test_expired_access_token_is_401(
    client: AsyncClient, session_factory: Factory, settings: SettingsSchema
) -> None:
    user = await seed_user(session_factory)
    expired = create_access_token(
        settings, user.user_id, now=datetime.now(UTC) - timedelta(days=1)
    )
    response = await client.get("/whoami", headers=_bearer(expired.token))
    assert response.status_code == 401


async def test_refresh_token_as_bearer_is_401(
    client: AsyncClient, session_factory: Factory
) -> None:
    user = await seed_user(session_factory)
    refresh = str((await _login(client, user))["refresh_token"])
    assert (
        await client.get("/whoami", headers=_bearer(refresh))
    ).status_code == 401


async def test_alg_none_is_401(
    client: AsyncClient, session_factory: Factory, settings: SettingsSchema
) -> None:
    user = await seed_user(session_factory)

    def b64(data: dict[str, object]) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": str(user.user_id),
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + 600,
        "jti": "x",
        "typ": "access",
    }
    token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}."
    assert (
        await client.get("/whoami", headers=_bearer(token))
    ).status_code == 401
    forged = jwt.encode(
        payload, "another-secret-key-of-32-characters!", "HS256"
    )
    assert (
        await client.get("/whoami", headers=_bearer(forged))
    ).status_code == 401


async def test_existing_routers_are_not_protected_yet(
    app: FastAPI,
) -> None:
    protected = {
        getattr(route, "path", "")
        for route in app.routes
        if getattr(route, "dependant", None)
        and any(
            d.call is get_principal
            for d in route.dependant.dependencies  # type: ignore[attr-defined]
        )
    }
    assert protected == {"/whoami"}
