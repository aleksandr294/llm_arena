"""Migration test against a scratch database: needs PostgreSQL
(``DATABASE_URL``) and permission to create databases."""

from alembic import command

from tests.migrations.helpers import alembic_config, run_sql, schema_drift


def _tables(url: str) -> set[str]:
    rows = run_sql(
        url,
        "SELECT tablename FROM pg_tables WHERE schemaname = "
        "current_schema() AND tablename <> 'alembic_version'",
    )
    return {name for (name,) in rows}


def test_upgrade_to_head_matches_models_and_downgrades_to_base(
    scratch_url: str,
) -> None:
    command.upgrade(alembic_config(), "head")

    assert "experiments" in _tables(scratch_url)
    # Schema, including column types, equals the models at head.
    assert schema_drift(scratch_url) == []

    command.downgrade(alembic_config(), "base")

    assert _tables(scratch_url) == set()
