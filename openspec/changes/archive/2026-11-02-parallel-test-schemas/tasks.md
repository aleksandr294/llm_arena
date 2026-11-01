# Tasks

## 1. Dependency and schema helper

- [x] 1.1 Add `pytest-xdist` to the dev group (`poetry add --group dev pytest-xdist`); verify `poetry run pytest --help` lists `-n`.
- [x] 1.2 In `tests/helpers.py` add the worker schema name helper and the connect-event installer that sets `search_path`; add unit tests (name from `gw<N>`, none for a serial run, rejection of an unexpected worker id) and verify they pass without PostgreSQL.

## 2. Per-worker schema lifecycle

- [x] 2.1 In the root `conftest.py` install the event and add the session-scoped fixture that guards the URL, recreates the schema before the first test and drops it after the last; verify with a test that, under `-n 2`, each worker sees only its own schema and that the schema is gone afterwards.
- [x] 2.2 Install the schema routing in the panel child (`tests/admin/panel_child.py`) and the uvicorn child, which inherit the worker id; verify the admin panel tests pass under `-n 2`.
- [x] 2.3 Verify the guard: with a non-test database name no schema is created or dropped (`tests/test_worker_schema_db.py`).

## 3. Verification and docs

- [x] 3.1 Run the full suite serially and with `-n 4` against a test database; verify equal results and no leftover `test_gw*` schemas. Run Ruff, Black and mypy.
- [x] 3.2 Document `pytest -n auto`, the schema naming, cleanup of aborted runs and the serial fallback in `docs/testing.md`; verify the documented commands run as written.

## Workflow follow-up

- Create the feature branch from an up-to-date `main` (the working tree currently holds the unfinished `extend-admin-panel-management` change on `feat/admin-panel-management`).
- Review with the reviewer agent, move Kan card `cfssnpkksdzl` to In-progress at start and Review after review.
- Archive the change after the project's review requirements are satisfied.
