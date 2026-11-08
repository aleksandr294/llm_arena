# Tasks

Branch: `feat/tech-debt-core-api-cleanups` (worktree `../llm-arena-tech-debt-core`).
Kan cards: `pyor7ltgb143`, `gba8wdsj9wnt`, `6wbgk022agsg`.

## 1. Core: settings, commit, repository

- [ ] 1.1 Add the setting validators and the `api_docs_enabled=False` default in `components/core/config.py`, update `.env.example`; extend `tests/test_config.py` (zero timeout, TTL ceilings, driver and scheme checks, low-entropy secrets for JWT and admin, error text never echoes values); verify every existing fixture still builds valid settings.
- [ ] 1.2 Make `commit()` drop callbacks on a failed commit and log all but the first callback failure; add tests in `tests/test_database.py` (two failing callbacks, failed commit then later commit); verify `tests/core/test_commit_discipline.py` still passes.
- [ ] 1.3 Remove `BaseRepository.list` and the overrides in `DatasetRepository`/`ExperimentRepository` after `grep -rn "\.list(" components api admin` confirms no caller; verify the suite and mypy.
- [ ] 1.4 Replace `Any` typing in `components/core/access.py` with a `Protocol` (or the documented fallback); verify `mypy` is clean and the access tests pass.
- [ ] 1.5 Update `docs/security.md` (findings register, A05 row for docs default and headers) and `docs/api-documentation.md`/README for the new default; verify links resolve.

## 2. HTTP layer

- [ ] 2.1 Pin the CSP CDN paths, send `Connection: close` only on HTTP/1, add the three new headers in `api/middleware.py`; update `tests/core/test_middleware.py` (full-URL source check, HTTP/2 scope without the header, new headers on success, error and 413); verify `/docs` and `/redoc` still render in the existing page tests.
- [ ] 2.2 Close the middleware test gaps (streaming response with over-limit body, app swallowing the receive error, single `Cache-Control`) and pin the fastadmin cookie name; verify the new tests fail against the old behavior where applicable (note which).
- [ ] 2.3 Move `Limit`, `Offset` (upper bound) and `not_found` to `api/v1/utils.py`, bound the `evaluator_id` filter, update the five endpoint modules; add 422 tests for offset and evaluator id; verify `grep -rn "def _not_found\|^Limit =\|^Offset =" api` shows only `utils.py`.
- [ ] 2.4 Document that `TrustedHost` is an operator concern and the accepted CDN risk in `docs/security.md`; verify the register entries match the code.

## 3. Lifecycle and CLI

- [ ] 3.1 Add `settings is None` handling, a `lifespan` that shuts the broker down and disposes the engine, and a loop-safe broker lock in `taskiq_app.py`; add tests (shutdown after a publish, two event loops); verify `tests/test_task_runtime.py` passes.
- [ ] 3.2 Build one `LLMService` per app on `app.state`, add `LLMService.aclose()`, make `ready` and `get_llm_service` use `get_settings_dependency`; update test overrides; verify two requests receive the same instance and shutdown closes the adapters' clients.
- [ ] 3.3 Dispose the engine and print one-line database errors in `cli.py`; add `tests/auth/test_cli.py` cases (database unreachable: exit 1, no URL, no traceback); verify the existing CLI tests pass.

## 4. Dataset limits

- [ ] 4.1 Add `max_cases_per_dataset` (config, `.env.example`), `DatasetFullError` and the check in `DatasetService.add_case`; pass settings through `build_dataset_service(session, settings)` and update callers; add service/API tests (limit boundary, 409, concurrent adds at the limit, nothing stored); verify `tests/core/test_commit_discipline.py` still passes.
- [ ] 4.2 Add `validate_metadata_bounds` to `components/core/utils.py` and use it in `MetadataDict`; add boundary tests (depth 5/6, 100/101 keys, 16 KiB/+1) and hostile-input tests from `tests/security/helpers.py`; verify the 422 names the metadata field.
- [ ] 4.3 Document the limits in `docs/datasets-api.md`; verify the documented numbers match the constants.

## 5. Integration

- [ ] 5.1 Run `pytest`, `ruff check`, `black --check`, `mypy`, Bandit and `openspec validate tech-debt-core-api-cleanups --strict`; report real results.

## Workflow follow-up

- Move the three Kan cards to `In-progress` at start and to `Review` after the reviewer agent.
- Merge conflicts in `config.py` and `database.py` with sibling changes are additive; resolve at rebase.
- Archive the change after review requirements are satisfied.
