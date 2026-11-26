"""Migration test against a scratch database: needs PostgreSQL
(``DATABASE_URL``) and permission to create databases."""

import uuid

from alembic import command

from tests.migrations.helpers import alembic_config, run_sql, seed_legacy_rows

INDEXES = {
    ("experiments", "ix_experiments_dataset_id"),
    ("experiment_results", "ix_experiment_results_test_case_id"),
}


def _fk_indexes(url: str) -> set[tuple[str, str]]:
    rows = run_sql(
        url,
        "SELECT tablename, indexname FROM pg_indexes WHERE indexname IN "
        "('ix_experiments_dataset_id', 'ix_experiment_results_test_case_id')",
    )
    return {(table, name) for table, name in rows}


def test_upgrade_adds_indexes_and_downgrade_drops_them(
    scratch_url: str,
) -> None:
    command.upgrade(alembic_config(), "0005")
    _, experiment_id = seed_legacy_rows(scratch_url)  # pre-0006 schema
    command.upgrade(alembic_config(), "0007")
    assert _fk_indexes(scratch_url) == set()

    command.upgrade(alembic_config(), "0010")

    assert _fk_indexes(scratch_url) == INDEXES
    assert run_sql(scratch_url, "SELECT id FROM experiments") == [
        (uuid.UUID(experiment_id),)
    ]
    assert run_sql(scratch_url, "SELECT count(*) FROM experiment_results") == [
        (1,)
    ]

    command.downgrade(alembic_config(), "0007")

    assert _fk_indexes(scratch_url) == set()
    assert run_sql(scratch_url, "SELECT count(*) FROM experiment_results") == [
        (1,)
    ]
