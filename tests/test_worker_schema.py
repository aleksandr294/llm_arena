"""Worker schema naming and connection routing (no database needed)."""

from typing import Any

import pytest

from tests.helpers import (
    WORKER_ENV,
    SchemaRouting,
    database_of,
    worker_schema,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WORKER_ENV, raising=False)


@pytest.mark.parametrize(
    ("worker", "schema"), [("gw0", "test_gw0"), ("gw12", "test_gw12")]
)
def test_schema_is_named_after_the_worker(
    monkeypatch: pytest.MonkeyPatch, worker: str, schema: str
) -> None:
    monkeypatch.setenv(WORKER_ENV, worker)
    assert worker_schema() == schema


@pytest.mark.parametrize("worker", [None, "master"])
def test_serial_run_has_no_schema(
    monkeypatch: pytest.MonkeyPatch, worker: str | None
) -> None:
    if worker is not None:
        monkeypatch.setenv(WORKER_ENV, worker)
    assert worker_schema() is None


@pytest.mark.parametrize(
    "bad",
    ["gw", "gw1; DROP SCHEMA public", 'gw1"', "../gw1", "GW1", "gw\u0661"],
)
def test_unexpected_worker_id_is_rejected(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv(WORKER_ENV, bad)
    with pytest.raises(ValueError, match="unexpected"):
        worker_schema()


def test_routing_sets_search_path_for_the_test_database_only() -> None:
    routing = SchemaRouting("test_gw0", "arena_test")
    ours: dict[str, Any] = {"database": "arena_test"}
    other: dict[str, Any] = {"database": "mig_abc"}
    routing._route(None, None, (), ours)
    routing._route(None, None, (), other)
    assert ours["server_settings"] == {
        "search_path": "test_gw0",
        "application_name": "test_gw0",
    }
    assert "server_settings" not in other


def test_routing_keeps_other_server_settings() -> None:
    routing = SchemaRouting("test_gw0", "arena_test")
    params: dict[str, Any] = {
        "database": "arena_test",
        "server_settings": {"application_name": "x"},
    }
    routing._route(None, None, (), params)
    assert params["server_settings"] == {
        "application_name": "test_gw0",
        "search_path": "test_gw0",
    }


def test_database_of_url() -> None:
    assert database_of("postgresql+asyncpg://u:p@h/arena_test") == "arena_test"
