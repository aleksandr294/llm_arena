# Proposal

## Why

The final quality audit (Kan card `iuzr94cp2k08`) found that the suite is not
hermetic: 20 `SettingsSchema(` calls in tests do not pass `_env_file=None`, so a
developer's `.env` changes results; the `integration` marker is applied by
hand in 43 modules; migration fixtures live in `test_0006` and are imported by
`test_0007`; `TEST_JWT_SECRET` is imported from `conftest` in 7 modules;
several tests assert nothing; and some tests depend on wall-clock timing.
One full run once ended with 20 unexplained errors that did not reproduce.
This hurts every other change in this batch, because they all add tests.

## What Changes

- A `make_settings()` helper builds settings with `_env_file=None`; a guard
  test fails when a test builds `SettingsSchema` any other way.
- Tests that use database or broker fixtures are marked `integration`
  automatically at collection; `--strict-markers` and `-rfE` are enabled so
  typos fail and error names are always printed.
- Migration fixtures and helpers move out of `test_0006` (fixture to the root
  `conftest.py`, functions to `tests/migrations/helpers.py`); `TEST_JWT_SECRET`
  moves to `tests/helpers.py`.
- Vacuous tests get real assertions; broad `except` and `pytest.raises(Exception)`
  are narrowed.
- Timing-dependent tests use events and deadlines instead of sleeps and
  fixed hangs.
- Pre-commit runs `ruff --fix` before `black`.

## Capabilities

### New Capabilities
- `test-isolation`: the test suite does not depend on the developer
  environment, hand-applied markers or wall-clock timing.

### Modified Capabilities
None.

## Impact

- Code: `conftest.py`, `tests/helpers.py`, `tests/migrations/*`, about 40 test
  modules (mechanical), `pyproject.toml` (`addopts`), `.pre-commit-config.yaml`.
- No production code, no migration, no dependency change.
- Docs: `docs/testing.md`.
- Merge note: this change edits tests that the auth change also touches
  (`tests/auth/test_security.py`, `tests/auth/test_service.py`); land this one
  first or rebase the auth branch over it (see design.md).
