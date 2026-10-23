"""CLI tests. Synchronous because the command runs ``asyncio.run`` itself;
need PostgreSQL (``DATABASE_URL``)."""

import asyncio
import logging
import sys
import uuid
from collections.abc import Iterator
from typing import cast

import pytest
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from typer.testing import CliRunner, Result

import cli
import main
from api.app import create_app
from components.auth.dependencies import build_auth_service
from components.core import database
from components.core.config import SettingsSchema
from components.core.database import Base
from components.core.enums import Role
from components.users.models import User
from tests.auth.helpers import MODELS
from tests.helpers import checked_database_url

pytestmark = pytest.mark.integration

PASSWORD = "a-strong-passphrase-1"
EMAIL = "Root@Example.com"
runner = CliRunner()


def _engine() -> AsyncEngine:
    return create_async_engine(checked_database_url(), poolclass=NullPool)


def _run_ddl(create: bool) -> None:
    async def go() -> None:
        engine = _engine()
        tables = [cast(Table, m.__table__) for m in MODELS]
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=tables)
            if create:
                await conn.run_sync(Base.metadata.create_all, tables=tables)
        await engine.dispose()

    asyncio.run(go())


@pytest.fixture(autouse=True)
def _db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _run_ddl(create=True)
    # NullPool: connections never outlive the loop of one asyncio.run call.
    factory = async_sessionmaker(_engine(), expire_on_commit=False)
    monkeypatch.setattr(database, "get_session_factory", lambda: factory)
    monkeypatch.delenv(cli.PASSWORD_ENV, raising=False)
    yield
    _run_ddl(create=False)


def _users() -> list[User]:
    async def go() -> list[User]:
        engine = _engine()
        async with async_sessionmaker(engine)() as s:
            rows = list((await s.scalars(select(User))).all())
        await engine.dispose()
        return rows

    return asyncio.run(go())


def _login(email: str, password: str) -> bool:
    settings = SettingsSchema(
        database_url="postgresql+asyncpg://unused/unused",
        rabbitmq_url="amqp://unused/",
    )

    async def go() -> bool:
        engine = _engine()
        try:
            async with async_sessionmaker(engine)() as s:
                await build_auth_service(s, settings).login(email, password)
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _all_output(result: Result) -> str:
    return str(result.output) + (
        str(result.stderr) if result.stderr_bytes else ""
    )


def test_create_superadmin_with_prompt() -> None:
    result = runner.invoke(
        cli.app,
        ["create-superadmin", "--email", EMAIL],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert result.exit_code == 0, result.output
    users = _users()
    assert len(users) == 1
    user = users[0]
    assert user.role == Role.SUPERADMIN.value
    assert user.organization_id is None and user.is_active
    assert user.password_hash != PASSWORD
    assert str(user.id) in result.output
    assert PASSWORD not in _all_output(result)
    assert _login("root@example.com", PASSWORD)


def test_create_superadmin_with_environment_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(cli.PASSWORD_ENV, PASSWORD)
    result = runner.invoke(cli.app, ["create-superadmin", "--email", EMAIL])
    assert result.exit_code == 0, result.output
    # Only the id is printed.
    assert uuid.UUID(result.stdout.strip()) == _users()[0].id
    assert PASSWORD not in _all_output(result)
    assert _login(EMAIL, PASSWORD)


def test_duplicate_email_refused_ignoring_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(cli.PASSWORD_ENV, PASSWORD)
    assert (
        runner.invoke(
            cli.app, ["create-superadmin", "--email", EMAIL]
        ).exit_code
        == 0
    )
    result = runner.invoke(
        cli.app, ["create-superadmin", "--email", EMAIL.lower()]
    )
    assert result.exit_code == 1
    assert "already exists" in _all_output(result)
    assert len(_users()) == 1


def test_weak_password_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cli.PASSWORD_ENV, "short")
    result = runner.invoke(cli.app, ["create-superadmin", "--email", EMAIL])
    assert result.exit_code == 1
    assert "password must be" in _all_output(result)
    assert "short" not in _all_output(result).replace("password must", "")
    assert _users() == []


@pytest.mark.parametrize(
    "email",
    [
        "not-an-email",
        "a@b",
        "a b@example.com",
        "@example.com",
        "x" * 250 + "@e.com",
    ],
)
def test_invalid_email_refused(
    monkeypatch: pytest.MonkeyPatch, email: str
) -> None:
    monkeypatch.setenv(cli.PASSWORD_ENV, PASSWORD)
    result = runner.invoke(cli.app, ["create-superadmin", "--email", email])
    assert result.exit_code == 1
    assert "email is not valid" in _all_output(result)
    assert PASSWORD not in _all_output(result)
    assert _users() == []


def test_prompt_confirmation_mismatch_creates_nothing() -> None:
    result = runner.invoke(
        cli.app,
        ["create-superadmin", "--email", EMAIL],
        input=f"{PASSWORD}\nother-passphrase-2\n",
    )
    assert result.exit_code != 0
    assert PASSWORD not in _all_output(result)
    assert _users() == []


def test_password_is_not_a_cli_option() -> None:
    result = runner.invoke(
        cli.app, ["create-superadmin", "--email", EMAIL, "--password", PASSWORD]
    )
    assert result.exit_code != 0
    assert _users() == []


def test_password_never_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(cli.PASSWORD_ENV, PASSWORD)
    with caplog.at_level(logging.DEBUG):
        result = runner.invoke(cli.app, ["create-superadmin", "--email", EMAIL])
    assert result.exit_code == 0
    user = _users()[0]
    assert PASSWORD not in caplog.text
    assert user.password_hash not in caplog.text
    assert user.password_hash not in _all_output(result)


def test_two_superadmins_with_same_password_have_different_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(cli.PASSWORD_ENV, PASSWORD)
    for email in ("a@example.com", "b@example.com"):
        assert (
            runner.invoke(
                cli.app, ["create-superadmin", "--email", email]
            ).exit_code
            == 0
        )
    first, second = _users()
    assert first.password_hash != second.password_hash
    assert PASSWORD not in first.password_hash


# --- main.py and HTTP surface ---------------------------------------------


def test_main_still_exposes_api_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    assert set(main.COMMANDS) == {"API"}
    monkeypatch.setitem(main.COMMANDS, "API", lambda: called.append("API"))
    monkeypatch.setattr(sys, "argv", ["main.py", "API"])
    main.main()
    assert called == ["API"]
    monkeypatch.setattr(sys, "argv", ["main.py", "create-superadmin"])
    with pytest.raises(SystemExit):
        main.main()


def test_no_http_route_creates_users() -> None:
    methods_by_path: dict[str, set[str]] = {
        getattr(r, "path", ""): getattr(r, "methods", set())
        for r in create_app().routes
    }
    for path, methods in methods_by_path.items():
        if "POST" in methods:
            assert "user" not in path and "superadmin" not in path, path
