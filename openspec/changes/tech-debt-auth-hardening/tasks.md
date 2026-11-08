# Tasks

Branch: `feat/tech-debt-auth-hardening` (worktree `../llm-arena-tech-debt-auth`).
Kan cards: `g836fdnsjp10`, `m7edal84snn0`, `70bigdwxma03`.

## 1. Settings, errors and migration

- [x] 1.1 Add settings `jwt_leeway_seconds`, `auth_max_failed_attempts`, `auth_lockout_seconds`, `max_active_experiments_per_organization` with bounds to `components/core/config.py` and `.env.example`; verify `tests/test_config.py` covers defaults and out-of-range values.
- [x] 1.2 Rename `InvalidTokenError`/`ExpiredTokenError` to `TokenInvalidError`/`TokenExpiredError`, add `AuthThrottledError` (carries retry-after seconds) and `ExperimentQuotaError`; update every import with no shim; verify `grep -rn "InvalidTokenError\|ExpiredTokenError" --include=*.py .` returns nothing and the suite imports cleanly.
- [x] 1.3 Add models (`User.tokens_valid_after`, `AuthThrottle`, `ix_refresh_tokens_expires_at`) and migration `0008` with upgrade/downgrade; add `tests/migrations/test_0008_auth_hardening.py` (upgrade from 0007 keeps existing users, downgrade restores 0007 schema); verify it passes against the scratch database.

## 2. Token lifetime and refresh

- [x] 2.1 Compare `iat` with `tokens_valid_after` in `AuthService._resolve` (reason `revoked`); set it on logout, password change and deactivation in the same transaction; add service and API tests (logout, password change, deactivation, new login accepted); verify `/auth/validate` reports `revoked`.
- [x] 2.2 Apply `jwt_leeway_seconds` in `decode_token`; add boundary tests (inside, beyond leeway); verify the logout expired-token path still works.
- [x] 2.3 Add `UserRepository.get_for_update`; use it in `AuthService.refresh` and `UserService.update_user`; add a concurrency test (deactivate vs refresh; role vs password update) on two sessions; verify neither outcome is lost.
- [x] 2.4 Hold the Argon2 semaphore slot until the hash thread finishes on cancellation; make `_DUMMY_HASH` lazy; add a test that a cancelled hash keeps the slot occupied and then frees it; verify timing-equalisation tests still pass.
- [x] 2.5 Add `RefreshTokenRepository.purge_expired(retention)` and `python cli.py purge-refresh-tokens`; add repository and CLI tests; verify the command prints the deleted count and leaves live records.
- [x] 2.6 Document double-submit refresh, logout semantics, leeway and the purge command in `docs/authentication.md`; verify the documented commands run as written.

## 3. Throttling and security events

- [x] 3.1 Implement the throttle repository and service (atomic upsert, lockout, reset on success, purge of stale rows) in `components/auth`; unit and integration tests for threshold, window expiry, unknown account parity and concurrent failures; verify `Retry-After` value.
- [x] 3.2 Wire the throttle into `/auth/login`, `/auth/refresh`, `/auth/validate` (address scope) and the admin `authenticate`; endpoints pass the client address; map `AuthThrottledError` to 429 with `Retry-After`; add API tests including the persisted failure counter after a 401; verify the correct password is rejected while locked.
- [x] 3.3 Add `log_security_event` and emit `auth.login_failed`, `auth.locked_out`, `auth.refresh_reuse`, `access.denied`, `admin.sign_in`; add tests for event names/fields and hostile-input tests using the corpus in `tests/security/helpers.py` (no raw email, no control characters); verify with `caplog`.
- [x] 3.4 Add the per-organization active-experiment cap in `ExperimentService.start` with org-row lock, 429 mapping and tests (limit reached, finished experiments not counted, concurrent starts); verify against `docs/experiments-api.md` and update it.
- [x] 3.5 Update `docs/security.md` (OWASP findings register: mark A07/API2/API4/A09 items resolved or documented) and README limitation text about missing rate limiting; verify links resolve.

## 4. Users and organizations services

- [x] 4.1 Replace `session.rollback()` with `begin_nested()` in `UserService` and `OrganizationService`; add the "unrelated pending change survives a conflict" tests; verify `grep -rn "\.rollback()" components/users components/organizations` returns nothing.
- [x] 4.2 Inject the session-revocation port into `OrganizationService` and remove the `components.auth` import; update `organizations/dependencies.py`, `admin/` and tests; verify `grep -rn "components.auth" components/organizations` is empty.
- [x] 4.3 Remove the dead superadmin branch in `UserService.create_user`; verify existing users API tests still pass unchanged.
- [x] 4.4 Document email-enumeration via 409 and peer-admin changes in `docs/authentication.md`; verify the text matches `docs/security.md`.

## 5. Integration

- [x] 5.1 Run `pytest`, `ruff check`, `black --check`, `mypy`, `ruff check --select S` on production code (command in `docs/security.md`) and `openspec validate tech-debt-auth-hardening --strict`; report real results.

## Workflow follow-up

- Move the three Kan cards to `In-progress` at start and to `Review` after the reviewer agent.
- After merge, re-parent `0009` and `0010` of the sibling changes onto `0008`.
- Archive the change after review requirements are satisfied.
