# Tasks

Branch: `feat/tech-debt-test-infrastructure` (worktree `../llm-arena-tech-debt-tests`).
Kan card: `iuzr94cp2k08`. Land first among the batch (mechanical edits touch many test files).

## 1. Hermetic settings and shared constants

- [ ] 1.1 Add `make_settings()` and `TEST_JWT_SECRET` to `tests/helpers.py`; make the root fixtures `settings` and `app` use it and have `conftest.py` import the constant from there; verify `pytest tests/test_config.py` passes with a deliberately hostile local `.env` (for example `LOG_LEVEL=DEBUG`, `MAX_REQUEST_BODY_BYTES=1`).
- [ ] 1.2 Replace the 20 direct `SettingsSchema(` calls and the seven `from conftest import TEST_JWT_SECRET` imports; verify `grep -rn "from conftest import" tests` is empty.
- [ ] 1.3 Add `tests/test_hermetic_settings.py` (AST scan, names file and line); verify it fails on a temporary offending call and passes on the tree.

## 2. Markers and diagnostics

- [ ] 2.1 Add the collection hook with `INTEGRATION_FIXTURES`, remove the manual `pytestmark` lines, set `addopts = "-rfE --strict-markers"`; verify `pytest -m "not integration"` passes with `DATABASE_URL` unset and the collected integration count equals the old manually marked count (record both numbers).
- [ ] 2.2 Add a test that an unregistered marker fails collection (run pytest in a subprocess on a temporary file); verify the assertion on the error text.

## 3. Migration fixtures

- [ ] 3.1 Move `scratch_url` to the root `conftest.py` and the helper functions to `tests/migrations/helpers.py`; update `test_0006` and `test_0007`; verify both pass and neither imports the other (`grep -n "test_0006" tests/migrations/test_0007*.py` is empty).

## 4. Meaningful tests

- [ ] 4.1 Fix the dataset, experiment-schema, auth security, auth service and CLI tests listed in design decision 5 and the `pytest.raises(Exception)` in `tests/experiments/test_repository.py`; verify each changed test fails when the behavior it names is broken (temporarily break it and record which tests failed).
- [ ] 4.2 Replace the timing dependence (panel readiness loop, concurrency probe, `_hang`); verify `tests/test_health.py` runs in under 1 s (`--durations=5`) and the three modules pass 20 times in a row (`pytest --count` is not installed: use a shell loop).

## 5. Tooling and docs

- [ ] 5.1 Put `ruff` before `black` in `.pre-commit-config.yaml`; verify `pre-commit run --all-files` leaves a clean tree on a formatted checkout.
- [ ] 5.2 Update `docs/testing.md` (hermetic settings helper, automatic markers, `-m "not integration"`, `-rfE`, Bandit and pip-audit commands); verify every documented command runs as written.

## 6. Integration

- [ ] 6.1 Run the full suite serially and with `-n auto` against a scratch database, `ruff check`, `black --check`, `mypy`, Bandit and `openspec validate tech-debt-test-infrastructure --strict`; report real results, including any error names printed by `-rfE`.

## Workflow follow-up

- Move the Kan card to `In-progress` at start and to `Review` after the reviewer agent; the card's comment about the unexplained 20-error run stays open until a run reproduces it with error names.
- Sibling branches rebase over this one before their own test work.
- Archive the change after review requirements are satisfied.
