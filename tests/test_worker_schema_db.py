"""Worker schemas on a real database: need PostgreSQL (``DATABASE_URL``)."""

import os
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.helpers import WORKER_ENV, worker_schema

pytestmark = pytest.mark.integration


async def test_unqualified_tables_resolve_to_the_workers_schema(
    engine: AsyncEngine,
) -> None:
    schema = worker_schema()
    async with engine.connect() as conn:
        current = (await conn.execute(text("SELECT current_schema()"))).scalar()
        # The schema an unqualified name resolves to.
        resolved = (
            await conn.execute(
                text(
                    "SELECT n.nspname FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid = c.relnamespace "
                    "WHERE c.oid = 'experiments'::regclass"
                )
            )
        ).scalar()
    if schema is None:  # serial run: the default schema, as before
        assert current == "public"
        assert resolved == "public"
    else:
        assert current == schema
        assert resolved == schema


async def test_other_workers_schemas_are_never_on_the_path(
    engine: AsyncEngine,
) -> None:
    async with engine.connect() as conn:
        path = (await conn.execute(text("SHOW search_path"))).scalar()
    schema = worker_schema()
    assert path == (schema if schema is not None else '"$user", public')


def test_a_non_test_database_is_refused_before_any_schema_is_touched() -> None:
    url = make_url(os.environ["DATABASE_URL"]).set(database="llm_arena_prod")
    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
    }
    env.pop(WORKER_ENV, None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-n",
            "1",
            "-p",
            "no:cacheprovider",
            "tests/test_worker_schema.py",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "must contain 'test'" in result.stdout + result.stderr
