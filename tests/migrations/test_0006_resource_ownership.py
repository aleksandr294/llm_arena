"""Migration test against a scratch database: needs PostgreSQL
(``DATABASE_URL``) and permission to create databases."""

import asyncio
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from components.core import config as app_config

pytestmark = pytest.mark.integration


def _run(url: str, sql: str, **params: Any) -> list[Any]:
    async def go() -> list[Any]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(sql), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _admin(sql: str) -> None:
    async def go() -> None:
        engine = create_async_engine(
            os.environ["DATABASE_URL"], isolation_level="AUTOCOMMIT"
        )
        try:
            async with engine.connect() as conn:
                await conn.execute(text(sql))
        finally:
            await engine.dispose()

    asyncio.run(go())


@pytest.fixture
def scratch_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"mig_{uuid.uuid4().hex[:12]}"
    _admin(f'CREATE DATABASE "{name}"')
    url = (
        make_url(os.environ["DATABASE_URL"])
        .set(database=name)
        .render_as_string(hide_password=False)
    )
    monkeypatch.setenv("DATABASE_URL", url)
    app_config.get_settings.cache_clear()
    yield url
    app_config.get_settings.cache_clear()
    monkeypatch.undo()
    _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _config() -> Config:
    return Config("alembic.ini")


def _drift(url: str) -> list[Any]:
    """``alembic check`` equivalent. Tables other tests register on the
    shared metadata (e.g. the persistence probe) have no migration, so only
    tables created by a migration are compared."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import components.auth.models  # noqa: F401
    import components.datasets.models  # noqa: F401
    import components.evaluation.models  # noqa: F401
    import components.experiments.models  # noqa: F401
    from components.core.database import Base

    def include(obj: Any, name: str | None, kind: str, *_: Any) -> bool:
        return not (kind == "table" and name == "persistence_probe")

    def compare(sync_conn: Any) -> list[Any]:
        ctx = MigrationContext.configure(
            sync_conn, opts={"include_object": include}
        )
        return list(compare_metadata(ctx, Base.metadata))

    async def go() -> list[Any]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(compare)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _seed_legacy_rows(url: str) -> tuple[str, str]:
    dataset_id, experiment_id = str(uuid.uuid4()), str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    _run(
        url,
        "INSERT INTO datasets (id, name) VALUES (:d, 'legacy')",
        d=dataset_id,
    )
    _run(
        url,
        "INSERT INTO test_cases (id, dataset_id, position, input) "
        "VALUES (:c, :d, 1, 'q')",
        c=case_id,
        d=dataset_id,
    )
    _run(
        url,
        "INSERT INTO experiments (id, name, dataset_id, prompt_template, "
        "status, config, evaluator_config) VALUES (:e, 'exp', :d, "
        "'{input}', 'completed', '{}'::jsonb, '{}'::jsonb)",
        e=experiment_id,
        d=dataset_id,
    )
    _run(
        url,
        "INSERT INTO experiment_results (id, experiment_id, test_case_id, "
        "case_position, provider, model, status, rendered_prompt) VALUES "
        "(:r, :e, :c, 1, 'openai', 'm', 'pending', 'p')",
        r=str(uuid.uuid4()),
        e=experiment_id,
        c=case_id,
    )
    return dataset_id, experiment_id


def test_upgrade_assigns_default_organization_and_downgrades(
    scratch_url: str,
) -> None:
    command.upgrade(_config(), "0005")
    dataset_id, experiment_id = _seed_legacy_rows(scratch_url)

    command.upgrade(_config(), "0006")

    orgs = _run(scratch_url, "SELECT id, name FROM organizations")
    assert [o[1] for o in orgs] == ["Default"]
    default_id = orgs[0][0]
    for table in ("datasets", "experiments"):
        rows = _run(
            scratch_url, f"SELECT organization_id, owner_id FROM {table}"
        )
        assert rows == [(default_id, None)]
    assert _run(scratch_url, "SELECT count(*) FROM experiment_results") == [
        (1,)
    ]
    assert _run(scratch_url, "SELECT count(*) FROM test_cases") == [(1,)]
    nullable = _run(
        scratch_url,
        "SELECT table_name, column_name, is_nullable FROM "
        "information_schema.columns WHERE column_name IN "
        "('organization_id', 'owner_id') AND table_name IN "
        "('datasets', 'experiments')",
    )
    assert {(t, c): n for t, c, n in nullable} == {
        ("datasets", "organization_id"): "NO",
        ("experiments", "organization_id"): "NO",
        ("datasets", "owner_id"): "YES",
        ("experiments", "owner_id"): "YES",
    }

    assert _drift(scratch_url) == []  # models and migrations agree

    command.downgrade(_config(), "0005")
    cols = _run(
        scratch_url,
        "SELECT count(*) FROM information_schema.columns WHERE "
        "column_name IN ('organization_id', 'owner_id') AND table_name IN "
        "('datasets', 'experiments')",
    )
    assert cols == [(0,)]
    assert _run(scratch_url, "SELECT id FROM datasets") == [
        (uuid.UUID(dataset_id),)
    ]
    assert _run(scratch_url, "SELECT id FROM experiments") == [
        (uuid.UUID(experiment_id),)
    ]
    assert _run(scratch_url, "SELECT count(*) FROM organizations") == [(0,)]


def test_downgrade_keeps_default_organization_with_users(
    scratch_url: str,
) -> None:
    command.upgrade(_config(), "0006")
    default_id = _run(scratch_url, "SELECT id FROM organizations")[0][0]
    _run(
        scratch_url,
        "INSERT INTO users (id, organization_id, email, email_lower, "
        "password_hash, role) VALUES "
        "(:u, :o, 'a@x.io', 'a@x.io', 'h', 'staff')",
        u=str(uuid.uuid4()),
        o=default_id,
    )
    command.downgrade(_config(), "0005")
    assert _run(scratch_url, "SELECT count(*) FROM organizations") == [(1,)]


def test_existing_default_named_organization_is_reused(
    scratch_url: str,
) -> None:
    command.upgrade(_config(), "0005")
    existing = str(uuid.uuid4())
    _run(
        scratch_url,
        "INSERT INTO organizations (id, name, name_lower) "
        "VALUES (:i, 'DEFAULT', 'default')",
        i=existing,
    )
    _seed_legacy_rows(scratch_url)
    command.upgrade(_config(), "0006")
    rows = _run(scratch_url, "SELECT organization_id FROM datasets")
    assert rows == [(uuid.UUID(existing),)]
    assert _run(scratch_url, "SELECT count(*) FROM organizations") == [(1,)]
