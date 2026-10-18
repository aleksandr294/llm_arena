# Tasks

## 1. Baseline

- [x] 1.1 Capture the baseline in a scratch location (not committed): full `pytest` result and test count, the generated `openapi.json`, `SettingsSchema` field names, registered Taskiq task names, the Alembic head, and an import-everything script that imports every module under `components`, `api`, `admin` and `cli.py` in a fresh interpreter; and verify the baseline numbers are recorded in the task notes

## 2. Shared definitions

- [x] 2.1 Create `components/core/enums.py` (`Role`), `components/core/exceptions.py` (`OrganizationRequiredError`), `components/core/schemas.py` (`Principal`) and `components/core/utils.py` (shared NUL-rejection helpers, replacing the cross-component private import), update every import including tests, and verify `ruff`, `mypy`, the import-everything script and the affected tests are green with no behavior change

## 3. Component modules

- [x] 3.1 Move exceptions, enums, public data classes and reusable helpers for datasets, organizations, users and auth to their `exceptions.py`, `enums.py`, `utils.py` and `schemas.py` per the move map in design.md (no re-exports, update all imports and tests), and verify the import check shows no cycles and the tests of these components pass unchanged
- [x] 3.2 Do the same for experiments, evaluation and metrics (including `ExportFormat` out of the endpoint module and `logic.py` helpers to `utils.py`), and verify the import check and the tests of these components pass unchanged
- [x] 3.3 Do the same for `third_party/llm` and the admin package (`SessionUser` and shared dataclasses), and verify the import check, `ruff`, `mypy` and the full test suite pass and the OpenAPI schema equals the baseline

## 4. Dependency injection

- [x] 4.1 Add `session_scope()` (async context manager: commit once on success, roll back on error, always close) to `components/core/database.py` and rebuild `get_session` on top of it with identical behavior, and add `on_commit(session, callback)` (callbacks run in registration order only after a successful commit, never on rollback; a failing callback does not undo the commit and its error reaches the caller), and verify tests for one commit per operation, rollback on exception, session closed, callback order and conditions, and that `get_session` behaves as before
- [x] 4.2 Make the request-scoped services (`DatasetService`, `ExperimentService`, `UserService`, `AuthService`, `OrganizationService`, `MetricsService`) rely on the session passed from the entry point with no mid-operation commits (`start`/`retry_failed` register the enqueue with `on_commit`; refresh-token reuse revokes, commits once and raises), inject their non-database collaborators through constructors (`UserService` gets `AuthService` and the organization lookup instead of building them; `prepare_export` gets its metrics collaborator), add small `dependencies.py` factories that the HTTP `Depends` functions, `cli.py` and the admin classes call, declare the session dependency with `scope="function"` through one shared alias used by every endpoint (commit or rollback before the response is sent) while keeping the existing override points, and verify the full suite passes, tests assert exactly one commit per operation, the enqueue-after-commit order and that a failed commit or a failed enqueue surfaces as an error response (not a success), a test shows the commit happens before the response is sent, a grep test shows no direct `session.commit(` outside `components/core/database.py`, and the OpenAPI schema equals the baseline
- [x] 4.3 Move session creation to the entry points for the flows that open their own sessions: Taskiq task functions open `session_scope()` and pass the session to `EvaluationService` (no session factory inside it), `ExperimentRunner` receives a `session_scope` callable from the task and runs claim, model call (no database access) and persist-plus-finalize as three atomic operations with today's lock ordering, export streaming gets `session_scope` from the endpoint for one short read-only session per batch, and admin classes and `cli.py` open `session_scope()` and pass the session down; make `LLMService` take its provider factories through the constructor (defaults unchanged); and verify with grep that only entry points read settings or build session factories, that no transaction is open during the model call, and that the full suite passes unchanged
- [x] 4.4 Add tests that demonstrate the injected collaborators are the ones used: `LLMService` with fake provider factories (no network), `ExperimentService` with a fake LLM and a recording `enqueue`, and `UserService` with a fake `AuthService`, and verify the ones that need no database run in the fast non-integration set

## 5. Tests layout

- [x] 5.1 Merge all shared fixtures into the single root `conftest.py` (one complete-schema `engine`, `session_factory`, app and clients, actors, shared panel fixtures), move single-module fixtures into their test modules, delete `tests/conftest.py` and every `tests/*/conftest.py`, and verify `pytest --collect-only` yields the same set of test ids as the baseline (apart from fixture locations) and the full suite passes
- [x] 5.2 Compare the suite runtime with the baseline; if the complete-schema engine slows the suite noticeably, switch the engine fixture to the cheaper strategy under the same name, and verify the final runtime and result in the task notes (measured in 5.1: about 316 s against about 310 s before, so the complete-schema engine needed no change of strategy)

## 6. Documentation

- [x] 6.1 Update the CLAUDE.md project-structure example (add `exceptions.py`, `enums.py`, `utils.py` per component, `components/core/{exceptions,enums,utils,schemas}.py`) and add short conventions for definition locations, dependency injection and the single conftest, and verify the file renders and matches the actual tree
- [x] 6.2 Rewrite `README.md` into Summary, Technical Stack, How to start locally, Commands, Architecture and Development methodology, move the existing per-feature reference verbatim into `docs/` pages linked from the README, and verify by running the documented local-start and command steps (compose or podman compose, migrations, API, worker, `cli.py create-superadmin`, tests, lint) and by comparing that every old README section still exists in `docs/`

## 7. Final checks

- [ ] 7.1 Run `pytest` (including integration tests), `ruff check .`, `black --check .`, `mypy`, the import-everything script and the baseline comparisons (test count, OpenAPI schema, settings fields, task names, Alembic head), and verify all match or are explained, noting any tests that could not run

## Workflow follow-up

- Move Kan card LLM-17 (`kfypgivlondn`) to In-progress when work starts and to Review once the reviewer agent has reviewed it (see the `kan-review` skill).
- Work on branch `refactor/code-structure` (stacked on `feat/admin-panel-and-api-docs`), commit per batch with the `git-conventional-commits` skill (`refactor:` and `docs:` types), do not push without being asked, and archive the change after approval.
