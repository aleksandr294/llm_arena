# Proposal

## Why

The integration suite drops and recreates every component table around each
test on one shared `DATABASE_URL`, so tests can only run one after another.
The suite keeps growing and a full run is slow. Kan card LLM-36 asks to
split the tests into groups that run in parallel, each group on its own
database schema, so groups cannot see or wipe each other's tables.

## What Changes

- Add `pytest-xdist` as a dev dependency. Each xdist worker is one test
  group.
- Give every worker its own PostgreSQL schema inside the test database
  (for example `test_gw0`). All connections the worker opens, including
  engines that tests build themselves and the admin panel child process,
  resolve unqualified table names in that schema only.
- Create the worker schema when the worker starts and drop it (with
  `CASCADE`) when the worker ends, so no state is left behind.
- Keep the existing guard: `DATABASE_URL` must still name a test database,
  and the schema is created only there.
- A run without xdist (plain `pytest`) keeps the current behavior and
  needs no schema.
- Document the parallel run in `docs/testing.md`.

No production code, API, or migration changes.

## Capabilities

### New Capabilities
- `parallel-test-execution`: how the test suite runs in parallel groups,
  each isolated on its own database schema, and what stays unchanged for a
  serial run.

### Modified Capabilities

None. The destructive-test guard stays as documented in `docs/testing.md`.

## Impact

- `pyproject.toml` / `poetry.lock`: new dev dependency `pytest-xdist`.
- `conftest.py`, `tests/helpers.py`, `tests/admin/panel_process.py`,
  `tests/admin/panel_child.py`: schema selection and setup.
- Tests that build an engine from `checked_database_url()` get the schema
  without edits.
- `docs/testing.md`: parallel run instructions.
- Migration tests that create scratch databases are already isolated by a
  unique database name and are out of scope.

## Assumptions

- "Groups" means xdist workers (`-n N`), one schema per worker. Fixed
  directory groups with a separate pytest process each were considered and
  rejected (see design.md).
