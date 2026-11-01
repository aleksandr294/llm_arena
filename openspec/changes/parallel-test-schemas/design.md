# Design

## Context

Integration tests rely on the root `conftest.py` `engine` fixture, which
drops and creates all component tables per test, and on several modules
that build an engine with `create_engine(checked_database_url())`
(`tests/test_database.py`, `tests/auth/*`, `tests/datasets/test_repository.py`).
The admin panel tests start a child interpreter (`tests/admin/panel_child.py`)
that reads `DATABASE_URL` from the environment. Migration tests create a
uniquely named scratch database and are already isolated.

The project supplies no schema option in `create_engine`, and production
code must not change for test needs.

## Goals / Non-Goals

**Goals:**
- Parallel test run with no cross-test table interference.
- No edits to individual test modules that already use
  `checked_database_url()`.
- Serial `pytest` behaves as today.

**Non-Goals:**
- Parallelizing the real-provider tests.
- A database per worker (needs `CREATEDB` rights and is slower).
- Changing the migration scratch-database tests.

## Decisions

1. **Use pytest-xdist; one worker is one group.** It is the standard way to
   run pytest in parallel and gives a stable worker id
   (`PYTEST_XDIST_WORKER`, `gw0`...). Alternative: split by directory and
   start one pytest process per group from a script. Rejected: uneven
   group sizes, a custom runner to maintain, and no automatic balancing.
   A new dev dependency is justified here (CLAUDE.md asks for a clear
   reason).

2. **Schema name from the worker id**, e.g. `test_gw0`; no worker id
   (serial run) means no schema is used. The name is built only from
   `gw` + digits, never from user input, and is quoted when used in DDL.

3. **Select the schema per connection, not per URL.** asyncpg has no
   URL option for `search_path`. A SQLAlchemy `do_connect` event on
   `Engine` passes `search_path` (and `application_name`, so tests can tell
   workers apart in `pg_stat_activity`) as asyncpg server settings. The path is the worker schema
   alone (no `public` fallback, so a leak into `public` fails loudly). It lives in a test
   helper (`tests/helpers.py`) and is installed once from the root
   `conftest.py`, so every engine in the process, including ones tests
   build themselves, is covered. Alternative: add a schema argument to
   `create_engine` and pass it from every test. Rejected: touches
   production code and about a dozen test modules.

4. **Schema lifecycle in a session-scoped autouse fixture** in the root
   `conftest.py`: `DROP SCHEMA IF EXISTS ... CASCADE`, `CREATE SCHEMA`,
   yield, `DROP SCHEMA ... CASCADE`. It runs the guard
   (`checked_database_url()`) first. It is skipped when there is no
   worker id, and for tests that do not need a database (no
   `DATABASE_URL`); it must not make unit tests require PostgreSQL.
   The fixture connects with a plain engine without the schema listener
   active for the DDL (DDL names the schema explicitly).

5. **Child processes find the schema through the inherited worker id**
   (`PYTEST_XDIST_WORKER`, kept by `child_environment()`); `panel_child.py`
   and the uvicorn launcher install the same routing at start. A separate
   schema variable was rejected: set in a developer's shell it would break
   the serial run.

## Risks / Trade-offs

- [PostgreSQL enum/extension objects live in `public`, which is no longer
  on the search path] → The schema creates its own types with its tables;
  verify during implementation that no model needs an extension from
  `public` (a failing test would show it at once).
- [Tests that assert on global state such as `information_schema` or
  table counts across schemas] → Filter by the current schema; check
  `tests/test_database.py` and the migration drift checks.
- [Schemas left after a killed run] → Recreated empty on the next start
  (decision 4); documented in `docs/testing.md`.
- [Port/rate or RabbitMQ shared state between workers] → Tests use
  mocked or unused broker URLs today; confirm no test needs a real shared
  broker.
- [Slower connection setup from the extra `SET`] → One statement per new
  pooled connection; negligible.

## Migration Plan

Dev tooling only. Roll out with `poetry add --group dev pytest-xdist`,
then `pytest -n auto`. Rollback: run `pytest` without `-n`, or remove the
dependency.
