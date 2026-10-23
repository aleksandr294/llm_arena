"""The destructive-test guard (no database needed)."""

import pytest

from tests.helpers import checked_database_url, database_name_problem

BASE = "postgresql+asyncpg://user:secret-pw@localhost:5432/"


@pytest.mark.parametrize(
    "name", ["llm_arena_test", "llm_arena_test_audit", "TEST", "mytestdb"]
)
def test_test_database_names_are_allowed(name: str) -> None:
    assert database_name_problem(BASE + name) is None


@pytest.mark.parametrize("name", ["llm_arena", "postgres", "prod", ""])
def test_other_database_names_are_refused_without_echoing_the_password(
    name: str,
) -> None:
    problem = database_name_problem(BASE + name)
    assert problem is not None and "test" in problem
    assert "secret-pw" not in problem


def test_unparsable_url_is_refused() -> None:
    assert database_name_problem("not a url") is not None


def test_checked_url_fails_the_test_for_a_dev_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", BASE + "llm_arena")
    with pytest.raises(pytest.fail.Exception, match="must contain 'test'"):
        checked_database_url()


def test_checked_url_returns_a_test_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", BASE + "llm_arena_test_audit")
    assert checked_database_url() == BASE + "llm_arena_test_audit"
