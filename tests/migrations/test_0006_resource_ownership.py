"""Migration test against a scratch database: needs PostgreSQL
(``DATABASE_URL``) and permission to create databases."""

import uuid

from alembic import command

from tests.migrations.helpers import (
    alembic_config,
    run_sql,
    seed_legacy_rows,
)


def test_upgrade_assigns_default_organization_and_downgrades(
    scratch_url: str,
) -> None:
    command.upgrade(alembic_config(), "0005")
    dataset_id, experiment_id = seed_legacy_rows(scratch_url)

    command.upgrade(alembic_config(), "0006")

    orgs = run_sql(scratch_url, "SELECT id, name FROM organizations")
    assert [o[1] for o in orgs] == ["Default"]
    default_id = orgs[0][0]
    for table in ("datasets", "experiments"):
        rows = run_sql(
            scratch_url, f"SELECT organization_id, owner_id FROM {table}"
        )
        assert rows == [(default_id, None)]
    assert run_sql(scratch_url, "SELECT count(*) FROM experiment_results") == [
        (1,)
    ]
    assert run_sql(scratch_url, "SELECT count(*) FROM test_cases") == [(1,)]
    nullable = run_sql(
        scratch_url,
        "SELECT table_name, column_name, is_nullable FROM "
        "information_schema.columns WHERE column_name IN "
        "('organization_id', 'owner_id') AND table_name IN "
        "('datasets', 'experiments')",
    )
    assert {(t, c): n for t, c, n in nullable} == {
        ("datasets", "organization_id"): "NO",
        ("experiments", "organization_id"): "NO",
        ("datasets", "owner_id"): "YES",
        ("experiments", "owner_id"): "YES",
    }

    # No drift check here: the models describe the head revision, which is
    # later than 0006. test_0007 asserts models and migrations agree at head.
    command.downgrade(alembic_config(), "0005")
    cols = run_sql(
        scratch_url,
        "SELECT count(*) FROM information_schema.columns WHERE "
        "column_name IN ('organization_id', 'owner_id') AND table_name IN "
        "('datasets', 'experiments')",
    )
    assert cols == [(0,)]
    assert run_sql(scratch_url, "SELECT id FROM datasets") == [
        (uuid.UUID(dataset_id),)
    ]
    assert run_sql(scratch_url, "SELECT id FROM experiments") == [
        (uuid.UUID(experiment_id),)
    ]
    assert run_sql(scratch_url, "SELECT count(*) FROM organizations") == [(0,)]


def test_downgrade_keeps_default_organization_with_users(
    scratch_url: str,
) -> None:
    command.upgrade(alembic_config(), "0006")
    default_id = run_sql(scratch_url, "SELECT id FROM organizations")[0][0]
    run_sql(
        scratch_url,
        "INSERT INTO users (id, organization_id, email, email_lower, "
        "password_hash, role) VALUES "
        "(:u, :o, 'a@x.io', 'a@x.io', 'h', 'staff')",
        u=str(uuid.uuid4()),
        o=default_id,
    )
    command.downgrade(alembic_config(), "0005")
    assert run_sql(scratch_url, "SELECT count(*) FROM organizations") == [(1,)]


def test_existing_default_named_organization_is_reused(
    scratch_url: str,
) -> None:
    command.upgrade(alembic_config(), "0005")
    existing = str(uuid.uuid4())
    run_sql(
        scratch_url,
        "INSERT INTO organizations (id, name, name_lower) "
        "VALUES (:i, 'DEFAULT', 'default')",
        i=existing,
    )
    seed_legacy_rows(scratch_url)
    command.upgrade(alembic_config(), "0006")
    rows = run_sql(scratch_url, "SELECT organization_id FROM datasets")
    assert rows == [(uuid.UUID(existing),)]
    assert run_sql(scratch_url, "SELECT count(*) FROM organizations") == [(1,)]
