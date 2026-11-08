"""Migration test against a scratch database: needs PostgreSQL
(``DATABASE_URL``) and permission to create databases."""

import uuid

import pytest
from alembic import command

from tests.migrations.test_0006_resource_ownership import (
    _config,
    _drift,
    _run,
    _seed_legacy_rows,
    scratch_url,  # noqa: F401  (fixture)
)

pytestmark = pytest.mark.integration


def test_upgrade_defaults_existing_rows_to_false_and_downgrades(
    scratch_url: str,  # noqa: F811
) -> None:
    command.upgrade(_config(), "0005")
    _, experiment_id = _seed_legacy_rows(scratch_url)  # pre-0006 schema
    command.upgrade(_config(), "0006")

    command.upgrade(_config(), "0007")

    assert _run(
        scratch_url, "SELECT response_truncated FROM experiment_results"
    ) == [(False,)]
    column = _run(
        scratch_url,
        "SELECT is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'experiment_results' "
        "AND column_name = 'response_truncated'",
    )
    assert column == [("NO", "false")]
    # Only the index added later by 0009 may be missing at revision 0007.
    assert [
        d
        for d in _drift(scratch_url)
        if not (
            d[0] == "add_index"
            and d[1].name == "ix_experiment_results_export_order"
        )
    ] == []

    command.downgrade(_config(), "0006")
    assert _run(
        scratch_url,
        "SELECT count(*) FROM information_schema.columns WHERE "
        "table_name = 'experiment_results' "
        "AND column_name = 'response_truncated'",
    ) == [(0,)]
    assert _run(
        scratch_url, "SELECT experiment_id FROM experiment_results"
    ) == [(uuid.UUID(experiment_id),)]
