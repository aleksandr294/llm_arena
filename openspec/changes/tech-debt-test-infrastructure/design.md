# Design

## Context

Settings in tests are built in 20 places with `SettingsSchema(...)` (the root
fixtures pass `_env_file=None` only in `app`). 43 modules declare
`pytestmark = pytest.mark.integration` by hand. `tests/migrations/test_0007...`
imports `scratch_url` and helpers from `test_0006...`. Seven modules do
`from conftest import TEST_JWT_SECRET`. CLAUDE.md allows exactly one
`conftest.py` at the root for fixtures shared by more than one module and
plain `tests/**/helpers.py` for helpers. The Kan card also records one
unexplained run with 20 errors (suspected timing/port dependent tests).
See proposal.md for motivation.

Branch `feat/tech-debt-test-infrastructure`, worktree
`../llm-arena-tech-debt-tests`. Production code is untouched.

## Goals / Non-Goals

**Goals:**
- Reproducible, environment-independent test runs; reliable markers;
  assertions that mean something; no sleeps standing in for synchronization.

**Non-Goals:**
- No change to what the tests cover, no new test framework or plugin.
- No removal of the deliberate `time.sleep` probe that overlaps hash threads
  (it is replaced by event synchronization, not deleted).
- Not fixing the unexplained 20-error run by guesswork: only making it
  diagnosable (`-rfE`) and removing the known timing suspects.

## Decisions

1. **`make_settings(**overrides)` in `tests/helpers.py`.** It fills placeholder
   URLs and `TEST_JWT_SECRET`, applies overrides and always passes
   `_env_file=None`. The 20 direct calls and the `settings` and `app`
   fixtures use it. A guard test `tests/test_hermetic_settings.py` parses test
   modules with `ast` and fails on a `SettingsSchema(...)` call without
   `_env_file`, like `tests/security/test_nosec_reasons.py` does for `# nosec`.
2. **Automatic `integration` marker** in the root `conftest.py`:
   `pytest_collection_modifyitems` adds the marker to items whose
   `fixturenames` include any of `engine`, `session_factory`, `api`, `actors`,
   `world`, `scratch_url` (a named constant `INTEGRATION_FIXTURES` next to the
   hook). The 43 manual `pytestmark` lines are removed. `addopts = "-rfE
   --strict-markers"` in `pyproject.toml`. Alternative: keep manual marks plus
   a guard test, rejected because the fixture set is the real reason a test
   needs infrastructure. Implementation addition: tests in a module that
   imports `checked_database_url` (own-engine tests) are marked too; this
   over-marks unit tests in the same module and does not catch aliased
   imports, so the CI job without services remains the backstop.
3. **Migration helpers.** `scratch_url` becomes a fixture in the root
   `conftest.py` (used by two modules); `_seed_legacy_rows`, `_drift`, SQL
   helpers move to `tests/migrations/helpers.py`. `test_0006` and `test_0007`
   import only from there. The deploy-and-migrations change adds a third
   migration test module on the same helpers and therefore merges after this one.
4. **`TEST_JWT_SECRET` in `tests/helpers.py`**; `conftest.py` and the seven
   modules import it from there. `conftest` keeps `os.environ.setdefault`
   using that value. (Importing from `conftest` by name is the fragile part:
   pytest loads it under a different module name in some invocations.)
5. **Vacuous tests**, one by one (the Kan card's list): give
   `test_dataset_limits_are_inclusive`/`test_case_limits_are_inclusive` the
   field-value assertions and a rejected `limit + 1` case;
   `test_same_model_on_different_providers_is_distinct` asserts both targets
   survive; `test_dummy_verification_completes_without_error` spies on the
   verify function and asserts it ran once on the dummy hash;
   `test_policy_accepts_boundaries` adds 11/129 rejections;
   `test_logout_is_idempotent` asserts the second logout succeeds and refresh
   still fails; the broad `except Exception` in `tests/auth/test_cli.py`
   becomes the specific exception types; `pytest.raises(Exception, match="lock")`
   in `tests/experiments/test_repository.py` becomes `DBAPIError`.
6. **Timing:** the panel readiness loop in `tests/admin/test_panel_server.py`
   polls with a deadline and puts the child's stderr into the failure message;
   the concurrency probe in `tests/auth/test_security.py` blocks on a
   `threading.Event` that the test sets once the expected number of calls is
   observed (bounded by a timeout), instead of `time.sleep(0.05)`; `_hang` in
   `tests/test_health.py` awaits a never-set `asyncio.Event`.
7. **Pre-commit order:** `ruff check --fix` before `black`, so formatting runs
   on the fixed code.

## Risks / Trade-offs

- [Auto-marking hides a missing `integration` mark on a test that connects
  without those fixtures] -> a test that opens its own engine must use
  `scratch_url` or the listed fixtures; the guard for such a test is the CI
  job without services (`-m "not integration"`) failing loudly.
- [Mechanical edits in ~40 files cause merge conflicts with sibling
  branches] -> land this change first or rebase siblings; edits only touch
  imports and marker lines.
- [`-rfE` makes output longer] -> acceptable; it is the diagnostic the audit
  asked for.

## Migration Plan

No data or deployment impact. Developers pull and re-run
`pytest -m "not integration"` without services to confirm.
