"""Migration test against a scratch database: needs PostgreSQL
(``DATABASE_URL``) and permission to create databases."""

from alembic import command

from tests.migrations.helpers import alembic_config, run_sql, schema_drift

_INDEX = "ix_experiment_results_export_order"


def _index_columns(url: str) -> list[str]:
    rows = run_sql(
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
    scratch_url: str,
) -> None:
    # 0009 follows 0010 (see the revision's docstring).
    command.upgrade(alembic_config(), "0010")
    assert _index_columns(scratch_url) == []

    command.upgrade(alembic_config(), "0009")

    assert _index_columns(scratch_url) == [
        "experiment_id",
        "case_position",
        "provider",
        "model",
        "id",
    ]
    assert schema_drift(scratch_url) == []

    command.downgrade(alembic_config(), "0010")
    assert _index_columns(scratch_url) == []
