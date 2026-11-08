"""Migration test against a scratch database: needs PostgreSQL
(``DATABASE_URL``) and permission to create databases."""

import pytest
from alembic import command

from tests.migrations.test_0006_resource_ownership import (
    _config,
    _drift,
    _run,
    scratch_url,  # noqa: F401  (fixture)
)

pytestmark = pytest.mark.integration

_INDEX = "ix_experiment_results_export_order"


def _index_columns(url: str) -> list[str]:
    rows = _run(
        url,
        "SELECT a.attname FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid "
        "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) "
        "ON true "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid "
        "AND a.attnum = k.attnum "
        "WHERE c.relname = :name ORDER BY k.ord",
        name=_INDEX,
    )
    return [row[0] for row in rows]


def test_upgrade_creates_the_export_index_and_downgrades(
    scratch_url: str,  # noqa: F811
) -> None:
    command.upgrade(_config(), "0007")
    assert _index_columns(scratch_url) == []

    command.upgrade(_config(), "0009")

    assert _index_columns(scratch_url) == [
        "experiment_id",
        "case_position",
        "provider",
        "model",
        "id",
    ]
    assert _drift(scratch_url) == []

    command.downgrade(_config(), "0007")
    assert _index_columns(scratch_url) == []
