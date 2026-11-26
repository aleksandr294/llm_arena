"""Migration test against a scratch database: needs PostgreSQL
(``DATABASE_URL``) and permission to create databases."""

import uuid

from alembic import command

from tests.migrations.helpers import (
    alembic_config,
    run_sql,
    seed_legacy_rows,
)


def test_upgrade_defaults_existing_rows_to_false_and_downgrades(
    scratch_url: str,
) -> None:
    command.upgrade(alembic_config(), "0005")
    _, experiment_id = seed_legacy_rows(scratch_url)  # pre-0006 schema
    command.upgrade(alembic_config(), "0006")

    command.upgrade(alembic_config(), "0007")

    assert run_sql(
        scratch_url, "SELECT response_truncated FROM experiment_results"
    ) == [(False,)]
    column = run_sql(
        scratch_url,
        "SELECT is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'experiment_results' "
        "AND column_name = 'response_truncated'",
    )
    assert column == [("NO", "false")]
    # Drift against the models is checked at head in test_round_trip.py.

    command.downgrade(alembic_config(), "0006")
    assert run_sql(
        scratch_url,
        "SELECT count(*) FROM information_schema.columns WHERE "
        "table_name = 'experiment_results' "
        "AND column_name = 'response_truncated'",
    ) == [(0,)]
    assert run_sql(
        scratch_url, "SELECT experiment_id FROM experiment_results"
    ) == [(uuid.UUID(experiment_id),)]
