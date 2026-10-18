"""Secrets never reach the logs. Need PostgreSQL (``DATABASE_URL``).

Every flow runs under DEBUG capture with sentinel values, then the captured
text is searched for tokens, passwords, hashes and submitted emails.
"""

import asyncio
import logging
import os
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from typer.testing import CliRunner

import cli
from components.auth.models import RefreshToken
from components.core import database
from components.core.enums import Role
from components.users.models import User
from tests.auth.conftest import PASSWORD, Factory, seed_user

pytestmark = pytest.mark.integration

UNKNOWN_EMAIL = "sentinel-unknown-account@example.com"
WRONG_PASSWORD = "sentinel-wrong-password-9f3a"
NEW_PASSWORD = "sentinel-new-password-7c1d"
CLI_PASSWORD = "sentinel-cli-password-5b2e"
CLI_EMAIL = "sentinel-cli-root@example.com"
BAD_BEARER = "sentinel.bad.bearer-token"
BAD_REFRESH = "sentinel.bad.refresh-token"


@pytest.fixture(autouse=True)
def _api(api: None) -> None:
    return None


def _assert_clean(caplog: pytest.LogCaptureFixture, *secrets: str) -> None:
    text = caplog.text
    for secret in secrets:
        assert secret, "empty sentinel would match everything"
        assert secret not in text


async def _post(
    client: AsyncClient, path: str, **kwargs: Any
) -> dict[str, Any]:
    response = await client.post(f"/api/v1{path}", **kwargs)
    assert response.status_code < 500, response.text
    return dict(response.json()) if response.content else {}


async def test_auth_flows_do_not_log_secrets(
    client: AsyncClient,
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = await seed_user(session_factory, "root@example.com", Role.SUPERADMIN)
    async with session_factory() as session:
        stored_hash = (await session.scalars(select(User))).one().password_hash
    secrets = [stored_hash, PASSWORD, WRONG_PASSWORD, UNKNOWN_EMAIL]

    with caplog.at_level(logging.DEBUG):
        # Failed logins: unknown account and wrong password.
        for email, password in (
            (UNKNOWN_EMAIL, WRONG_PASSWORD),
            (root.email, WRONG_PASSWORD),
        ):
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": password},
            )
            assert response.status_code == 401
        # Successful login, validate, refresh, reuse of the old token.
        pair = await _post(
            client,
            "/auth/login",
            json={"email": root.email, "password": PASSWORD},
        )
        access, refresh = pair["access_token"], pair["refresh_token"]
        validated = await _post(
            client, "/auth/validate", json={"token": access}
        )
        assert validated["valid"] is True
        rotated = await _post(
            client, "/auth/refresh", json={"refresh_token": refresh}
        )
        reuse = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh}
        )
        assert reuse.status_code == 401
        # Invalid tokens on every entry point.
        for path, body in (
            ("/auth/validate", {"token": BAD_BEARER}),
            ("/auth/refresh", {"refresh_token": BAD_REFRESH}),
            ("/auth/logout", {"refresh_token": BAD_REFRESH}),
        ):
            await client.post(f"/api/v1{path}", json=body)
        malformed = await client.get(
            "/api/v1/users", headers={"Authorization": f"Bearer {BAD_BEARER}"}
        )
        assert malformed.status_code == 401
        no_scheme = await client.get(
            "/api/v1/users", headers={"Authorization": BAD_BEARER}
        )
        assert no_scheme.status_code == 401
        # Logout with the live refresh token.
        logout = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": rotated["refresh_token"]},
        )
        assert logout.status_code == 204

    async with session_factory() as session:
        hashes = [
            r.token_hash for r in (await session.scalars(select(RefreshToken)))
        ]
    _assert_clean(
        caplog,
        *secrets,
        access,
        refresh,
        rotated["access_token"],
        rotated["refresh_token"],
        BAD_BEARER,
        BAD_REFRESH,
        *hashes,
    )


async def test_management_calls_do_not_log_secrets(
    client: AsyncClient,
    session_factory: Factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_user(session_factory, "root@example.com", Role.SUPERADMIN)
    new_email = "sentinel-new-user@example.com"
    taken_email = "sentinel-duplicate@example.com"

    with caplog.at_level(logging.DEBUG):
        pair = await _post(
            client,
            "/auth/login",
            json={"email": "root@example.com", "password": PASSWORD},
        )
        headers = {"Authorization": f"Bearer {pair['access_token']}"}
        org = await _post(
            client, "/organizations", json={"name": "Acme"}, headers=headers
        )
        listed = await client.get("/api/v1/organizations", headers=headers)
        assert listed.status_code == 200
        patched = await client.patch(
            f"/api/v1/organizations/{org['id']}",
            json={"name": "Acme 2"},
            headers=headers,
        )
        assert patched.status_code == 200
        admin = await _post(
            client,
            "/users",
            json={
                "email": new_email,
                "password": NEW_PASSWORD,
                "role": "admin",
                "organization_id": org["id"],
            },
            headers=headers,
        )
        # Duplicate email and weak password go through the error paths.
        for email, password in (
            (new_email, NEW_PASSWORD),
            (taken_email, "short"),
        ):
            await client.post(
                "/api/v1/users",
                json={
                    "email": email,
                    "password": password,
                    "role": "staff",
                    "organization_id": org["id"],
                },
                headers=headers,
            )
        changed = await client.patch(
            f"/api/v1/users/{admin['id']}",
            json={"password": WRONG_PASSWORD, "is_active": True},
            headers=headers,
        )
        assert changed.status_code == 200
        assert (
            await client.get("/api/v1/users", headers=headers)
        ).status_code == 200

    async with session_factory() as session:
        hashes = [u.password_hash for u in await session.scalars(select(User))]
    _assert_clean(
        caplog,
        pair["access_token"],
        pair["refresh_token"],
        PASSWORD,
        NEW_PASSWORD,
        WRONG_PASSWORD,
        *hashes,
    )


async def test_cli_does_not_log_secrets(
    session_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The command runs asyncio.run itself, so it gets its own thread and a
    # pool-less engine whose connections do not outlive that loop.
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "get_session_factory", lambda: factory)
    monkeypatch.setenv(cli.PASSWORD_ENV, CLI_PASSWORD)
    runner = CliRunner()

    with caplog.at_level(logging.DEBUG):
        ok = await asyncio.to_thread(
            runner.invoke, cli.app, ["create-superadmin", "--email", CLI_EMAIL]
        )
        duplicate = await asyncio.to_thread(
            runner.invoke, cli.app, ["create-superadmin", "--email", CLI_EMAIL]
        )
    await engine.dispose()

    assert ok.exit_code == 0 and duplicate.exit_code == 1
    async with session_factory() as session:
        stored_hash = (await session.scalars(select(User))).one().password_hash
    _assert_clean(caplog, CLI_PASSWORD, stored_hash)
