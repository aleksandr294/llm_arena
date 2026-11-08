"""Helpers for the migration tests (plain functions, not fixtures).

The ``scratch_url`` fixture that creates the scratch database is in the root
``conftest.py``.
"""

import asyncio
import uuid
from typing import Any

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.helpers import checked_database_url


def run_sql(url: str, sql: str, **params: Any) -> list[Any]:
    async def go() -> list[Any]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(sql), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(go())


def admin_sql(sql: str) -> None:
    async def go() -> None:
        engine = create_async_engine(
            checked_database_url(), isolation_level="AUTOCOMMIT"
        )
        try:
            async with engine.connect() as conn:
                await conn.execute(text(sql))
        finally:
            await engine.dispose()

    asyncio.run(go())


def alembic_config() -> Config:
    return Config("alembic.ini")


def schema_drift(url: str) -> list[Any]:
    """``alembic check`` equivalent. Tables other tests register on the
    shared metadata (e.g. the persistence probe) have no migration, so only
    tables created by a migration are compared."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import components.auth.models
    import components.datasets.models
    import components.evaluation.models
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


def seed_legacy_rows(url: str) -> tuple[str, str]:
    dataset_id, experiment_id = str(uuid.uuid4()), str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    run_sql(
        url,
        "INSERT INTO datasets (id, name) VALUES (:d, 'legacy')",
        d=dataset_id,
    )
    run_sql(
        url,
        "INSERT INTO test_cases (id, dataset_id, position, input) "
        "VALUES (:c, :d, 1, 'q')",
        c=case_id,
        d=dataset_id,
    )
    run_sql(
        url,
        "INSERT INTO experiments (id, name, dataset_id, prompt_template, "
        "status, config, evaluator_config) VALUES (:e, 'exp', :d, "
        "'{input}', 'completed', '{}'::jsonb, '{}'::jsonb)",
        e=experiment_id,
        d=dataset_id,
    )
    run_sql(
        url,
        "INSERT INTO experiment_results (id, experiment_id, test_case_id, "
        "case_position, provider, model, status, rendered_prompt) VALUES "
        "(:r, :e, :c, 1, 'openai', 'm', 'pending', 'p')",
        r=str(uuid.uuid4()),
        e=experiment_id,
        c=case_id,
    )
    return dataset_id, experiment_id
