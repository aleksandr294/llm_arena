## Quality checks

[Back to the README](../README.md)

```bash
pytest                    # integration tests need a test DATABASE_URL (below)
pytest -n auto            # parallel groups, one schema each (below)
pytest -m "not integration"   # unit tests only, no PostgreSQL or RabbitMQ needed
ruff check .
black --check .
mypy
poetry run ruff check --select S components api admin cli.py main.py \
  taskiq_app.py migrations migrations/versions/*.py scripts   # security rules, must be clean
poetry run pip-audit      # needs network; see docs/security.md
# The security rules are Ruff's S rules (the command above); Bandit was
# replaced by them, see docs/security.md
pre-commit run --all-files
```

## Hermetic tests

A test run must not depend on the developer's machine.

- **Settings.** Build settings with `tests.helpers.make_settings(**overrides)`
  (or use the root `settings` and `app` fixtures). It fills placeholder URLs
  that nothing connects to and the test JWT key, and always passes
  `_env_file=None`, so a local `.env` cannot change a result. A test that
  must control the environment itself builds `SettingsSchema(_env_file=None)`
  after `monkeypatch.setenv`/`delenv` (see `tests/test_config.py`).
  `tests/test_hermetic_settings.py` fails, naming file and line, when a test
  builds `SettingsSchema` without `_env_file`. The guard matches calls named
  `SettingsSchema(...)` only; it does not catch aliases,
  `SettingsSchema.model_validate` or the cached `get_settings()`, which reads
  `.env`, so do not use those in tests.
- **Shared constants.** `TEST_JWT_SECRET` lives in `tests/helpers.py`; never
  `from conftest import ...`.
- **Markers.** Do not write `pytest.mark.integration` by hand. The root
  `conftest.py` marks a test `integration` at collection when it uses one of
  the `INTEGRATION_FIXTURES` (`engine`, `session_factory`, `api`, `actors`,
  `world`, `scratch_url`, and fixtures built on them) or when its module
  imports `checked_database_url` (a test that opens its own engine must use
  it, see below). A test that needs the database in another way requests
  one of the fixtures with `@pytest.mark.usefixtures("engine")`. The one
  deliberate hand-applied mark is in `tests/test_worker_schema_db.py`, which
  reads `DATABASE_URL` without a fixture. The hook marks the whole module
  that imports `checked_database_url`, so a unit test in such a module is
  marked too; keep unit tests elsewhere.
- **Strict markers and diagnostics.** `pyproject.toml` sets
  `addopts = "-rfE --strict-markers"`: an unregistered marker fails
  collection (`tests/test_markers.py`) and the summary always lists failed
  and errored tests by name.
- **Unit tests without services.** `pytest -m "not integration"` needs no
  database or broker. Several modules still read settings at import, so
  `DATABASE_URL` and `RABBITMQ_URL` must be set to some value (never
  connected to), as they are in a normal `.env`.
- **Migration tests.** The `scratch_url` fixture (root `conftest.py`) creates
  and drops a scratch database; the SQL, Alembic and drift helpers are in
  `tests/migrations/helpers.py`.
- **Timing.** Synchronize with events and bounded deadlines, not with
  fixed sleeps or fixed-length hangs.

## Test database

Integration tests drop and create the component tables on `DATABASE_URL`.
They therefore refuse to run (the test fails with a clear message) unless the
database name in that URL contains `test` (case-insensitive), so exporting
your development `.env` can never wipe the development database. Create a
scratch database and point `DATABASE_URL` at it for the test run only:

```bash
docker compose exec postgres createdb -U <user> llm_arena_test
DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/llm_arena_test \
  pytest
```

The rule lives in `tests/helpers.py` (`checked_database_url`) and is used by
the root `conftest.py` and every test that builds an engine from
`DATABASE_URL`; a new test that does so must use it too.

## Parallel run

`pytest -n 4` (or `-n auto`) runs the tests in parallel with pytest-xdist.
Each worker is a test group with its own PostgreSQL schema in the test
database (`test_gw0`, `test_gw1`, ...), so groups never see or drop each
other's tables. A session fixture in the root `conftest.py` recreates the
schema when the worker starts and drops it when the worker ends. Every
connection to the test database, including engines a test builds itself and
the child processes of the admin panel tests, resolves table names in that
schema only (`public` is not on the path), and its `application_name` is the
schema name, so queries on `pg_stat_activity` can tell workers apart.

- Plain `pytest` (no `-n`) uses the default schema and needs no setup.
- The database-name guard above still applies; no schema is created on a
  database whose name lacks `test`. Under `-n` with such a `DATABASE_URL`
  every test errors at setup, unit tests included.
- After a killed run a leftover `test_gw*` schema is replaced on the next
  start. To remove them by hand:
  `DROP SCHEMA test_gw0 CASCADE;` in the test database.
- A test that starts a child process must pass on the schema: use
  `tests.admin.panel_process.child_environment()` and call
  `route_to_worker_schema()` in the child (see `tests/admin/uvicorn_child.py`).
- A test that queries `pg_stat_activity` must filter on
  `application_name = current_setting('application_name')`.

## Real-provider tests

Tests in `tests/llm/real/` call the real OpenAI and Anthropic APIs, cost
money, and are skipped by default. To run them:

```bash
RUN_REAL_PROVIDER_TESTS=1 OPENAI_API_KEY=... ANTHROPIC_API_KEY=... \
  pytest -m real_provider tests/llm/real
```

Each test is also skipped if its provider key is unset. Override the models
with `OPENAI_TEST_MODEL` / `ANTHROPIC_TEST_MODEL`.
