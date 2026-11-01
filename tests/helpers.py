"""Helpers shared by many test modules (plain functions, not fixtures)."""

import os
import re
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine, make_url

GUARD_WORD = "test"


def database_name_problem(url: str) -> str | None:
    """Why ``url`` is not safe for destructive tests, or ``None`` if it is.

    Integration tests drop and create tables, so they only run against a
    database whose name contains ``test`` (case-insensitive). The message
    never contains the URL, which holds a password.
    """
    try:
        name = make_url(url).database or ""
    except Exception:  # unparsable URL: say so without echoing it
        return "DATABASE_URL could not be parsed"
    if GUARD_WORD in name.lower():
        return None
    return (
        f"refusing to run destructive tests on database {name!r}: its name "
        f"must contain {GUARD_WORD!r}. Create a scratch database (for "
        "example llm_arena_test) and point DATABASE_URL at it; see "
        "docs/testing.md."
    )


def checked_database_url() -> str:
    """``DATABASE_URL`` if it names a test database, else fail the test."""
    url = os.environ["DATABASE_URL"]
    problem = database_name_problem(url)
    if problem is not None:
        pytest.fail(problem, pytrace=False)
    return url


WORKER_ENV = "PYTEST_XDIST_WORKER"
_WORKER_ID = re.compile(r"gw[0-9]+")


def worker_schema() -> str | None:
    """The schema of this pytest-xdist worker, ``None`` for a serial run.

    Child processes started by a test inherit ``PYTEST_XDIST_WORKER`` and
    so get the same name. The worker id is checked against a strict
    pattern, so the name is safe to use in DDL and never comes from free
    text.
    """
    worker = os.environ.get(WORKER_ENV)
    if worker is None or worker == "master":
        return None
    if not _WORKER_ID.fullmatch(worker):
        raise ValueError(f"unexpected {WORKER_ENV} value")
    return f"test_{worker}"


def database_of(url: str) -> str:
    return make_url(url).database or ""


class SchemaRouting:
    """Make every connection to ``database`` use ``schema`` alone.

    asyncpg has no URL option for ``search_path``, so it is passed as a
    server setting when a connection opens. This covers engines that tests
    build themselves. Connections to other databases (the migration tests'
    scratch databases) are left alone, and ``public`` is not on the path,
    so a test that escapes its schema fails instead of sharing tables.
    """

    def __init__(self, schema: str, database: str) -> None:
        self.schema = schema
        self.database = database

    def _route(
        self, dialect: Any, conn_rec: Any, cargs: Any, cparams: dict[str, Any]
    ) -> None:
        if cparams.get("database") == self.database:
            settings = dict(cparams.get("server_settings") or {})
            settings["search_path"] = self.schema
            # Lets a test tell its own connections from other workers' in
            # ``pg_stat_activity``, which is database-wide.
            settings["application_name"] = self.schema
            cparams["server_settings"] = settings

    def install(self) -> None:
        event.listen(Engine, "do_connect", self._route)

    def remove(self) -> None:
        event.remove(Engine, "do_connect", self._route)
