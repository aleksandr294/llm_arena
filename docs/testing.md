## Quality checks

[Back to the README](../README.md)

```bash
pytest                    # integration tests need a test DATABASE_URL (below)
pytest -n auto            # parallel groups, one schema each (below)
pytest -m "not integration"
ruff check .
black --check .
mypy
poetry run bandit -c pyproject.toml -r -ll components api admin cli.py \
  main.py taskiq_app.py migrations scripts   # medium and above must be clean
poetry run pip-audit      # needs network; see docs/security.md
pre-commit run --all-files
```

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
