"""Migration test against a scratch database: needs PostgreSQL
(``DATABASE_URL``) and permission to create databases."""

from alembic import command

from tests.migrations.helpers import alembic_config, run_sql, schema_drift


def test_upgrade_keeps_users_and_downgrade_restores_0007(
    scratch_url: str,
) -> None:
    command.upgrade(alembic_config(), "0007")
    run_sql(
        scratch_url,
        "INSERT INTO users (id, email, email_lower, password_hash, role) "
        "VALUES (gen_random_uuid(), 'a@b.co', 'a@b.co', 'x', 'superadmin')",
    )

    command.upgrade(alembic_config(), "0008")

    assert run_sql(scratch_url, "SELECT tokens_valid_after FROM users") == [
        (None,)
    ]
    assert run_sql(
        scratch_url,
        "SELECT count(*) FROM pg_indexes "
        "WHERE indexname = 'ix_refresh_tokens_expires_at'",
    ) == [(1,)]
    run_sql(
        scratch_url,
        "INSERT INTO auth_throttle (scope, key_hash, failures) "
        "VALUES ('account', repeat('a', 64), 1)",
    )
    assert schema_drift(scratch_url) == []

    command.downgrade(alembic_config(), "0007")

    assert run_sql(
        scratch_url,
        "SELECT count(*) FROM information_schema.columns WHERE "
        "table_name = 'users' AND column_name = 'tokens_valid_after'",
    ) == [(0,)]
    assert run_sql(
        scratch_url,
        "SELECT count(*) FROM pg_indexes "
        "WHERE indexname = 'ix_refresh_tokens_expires_at'",
    ) == [(0,)]
    assert run_sql(
        scratch_url, "SELECT to_regclass('auth_throttle') IS NULL"
    ) == [(True,)]
    assert run_sql(scratch_url, "SELECT email FROM users") == [("a@b.co",)]
