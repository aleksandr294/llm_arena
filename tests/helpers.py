"""Helpers shared by many test modules (plain functions, not fixtures)."""

import os

import pytest
from sqlalchemy.engine import make_url

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
