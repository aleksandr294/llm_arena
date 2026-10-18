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

- [ ] 4.1 Add `components/core/uow.py` (`UnitOfWork` base: async context manager over the session factory, `commit`/`rollback`/`flush`, `after_commit(callback)` executed in order after a successful commit, rollback on exception or missing commit, session always closed) and the domain Units of Work (datasets, experiments, evaluation, metrics, and one identity UoW for auth, users and organizations) exposing typed repositories bound to the one session, and verify tests for one commit per operation, rollback on exception, rollback when left uncommitted, session closed, callbacks running only after a successful commit and in order, a failing callback not undoing the commit, and repositories sharing the session
- [ ] 4.2 Convert the request-scoped services (`DatasetService`, `ExperimentService`, `UserService`, `AuthService`, `OrganizationService`, `MetricsService`) to receive a UoW instead of a session and loose repositories (no hidden defaults), remove mid-operation commits (`start`/`retry_failed` register the enqueue with `after_commit`; refresh-token reuse revokes, commits once and raises afterwards), add `dependencies.py` factories, make the HTTP `Depends` functions yield the UoW with the same commit-at-end/rollback-on-error behavior and the same override points, and update `cli.py` and the admin classes to open one UoW per operation; verify the full suite passes, tests assert exactly one commit per operation and the enqueue order, and the OpenAPI schema equals the baseline
- [ ] 4.3 Convert the flows that own transactions: Taskiq task functions open the UoW and pass it down (evaluation task: one UoW), `ExperimentRunner` receives a UoW factory and runs claim, model call (no database access) and persist-plus-finalize as three separate atomic operations with today's lock ordering, and export streaming uses a UoW factory with one short read-only UoW per batch; make `LLMService` take its provider factories through the constructor (defaults unchanged); and verify with grep that only entry points read settings or build session factories and UoWs, that no transaction is open during the model call, and that the full suite passes unchanged
- [ ] 4.4 Add fake-based unit tests that construct `ExperimentService`, `UserService` and `LLMService` with fake UoWs and in-memory fakes (no database, no network) and exercise one path each, demonstrating the injected collaborators are the ones used, and verify they run in the fast non-integration set

## 5. Tests layout

- [ ] 5.1 Merge all shared fixtures into the single root `conftest.py` (one complete-schema `engine`, `session_factory`, app and clients, actors, shared panel fixtures), move single-module fixtures into their test modules, delete `tests/conftest.py` and every `tests/*/conftest.py`, and verify `pytest --collect-only` yields the same set of test ids as the baseline (apart from fixture locations) and the full suite passes
- [ ] 5.2 Compare the suite runtime with the baseline; if the complete-schema engine slows the suite noticeably, switch the engine fixture to the cheaper strategy under the same name, and verify the final runtime and result in the task notes

## 6. Documentation

- [ ] 6.1 Update the CLAUDE.md project-structure example (add `exceptions.py`, `enums.py`, `utils.py` per component, `components/core/{exceptions,enums,utils,schemas}.py`) and add short conventions for definition locations, dependency injection and the single conftest, and verify the file renders and matches the actual tree
- [ ] 6.2 Rewrite `README.md` into Summary, Technical Stack, How to start locally, Commands, Architecture and Development methodology, move the existing per-feature reference verbatim into `docs/` pages linked from the README, and verify by running the documented local-start and command steps (compose or podman compose, migrations, API, worker, `cli.py create-superadmin`, tests, lint) and by comparing that every old README section still exists in `docs/`

## 7. Final checks

- [ ] 7.1 Run `pytest` (including integration tests), `ruff check .`, `black --check .`, `mypy`, the import-everything script and the baseline comparisons (test count, OpenAPI schema, settings fields, task names, Alembic head), and verify all match or are explained, noting any tests that could not run

## Workflow follow-up

- Move Kan card LLM-17 (`kfypgivlondn`) to In-progress when work starts and to Review once the reviewer agent has reviewed it (see the `kan-review` skill).
- Work on branch `refactor/code-structure` (stacked on `feat/admin-panel-and-api-docs`), commit per batch with the `git-conventional-commits` skill (`refactor:` and `docs:` types), do not push without being asked, and archive the change after approval.
