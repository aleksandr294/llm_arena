# Design

## Context

Auth lives in `components/auth` (service, security helpers, refresh token
repository), `components/users` and `components/organizations`. Access tokens
are resolved from the database on every request (`AuthService._resolve`), but
the token itself carries only `iat`/`exp`, so logout and password change do
not end it. Throttling does not exist: Argon2 concurrency is capped at 4
slots per process (`security.py`) and the README lists this as a limitation.
Services call `session.rollback()` after `IntegrityError`, which rolls back the
caller's whole transaction (CLAUDE.md: the entry point owns it). See
proposal.md for motivation.

The three Kan cards of this change touch the same modules, so one branch
(`feat/tech-debt-auth-hardening`, worktree `../llm-arena-tech-debt-auth`) owns
them. Merge order across the six Tech Debt changes: `tech-debt-test-infrastructure`
first (mechanical edits in many test files, which this branch also touches),
then **this change** (it adds the first migration, `0008`), then
`tech-debt-experiment-pipeline` (`0009`) and `tech-debt-deploy-and-migrations`
(`0010`); `tech-debt-core-api-cleanups` and `tech-debt-llm-providers` have no
ordering constraint.

## Goals / Non-Goals

**Goals:**
- Access tokens die with the session; credential endpoints are throttled;
  security outcomes are logged; one tenant cannot flood the queue.
- Services stop rolling back the caller's transaction.

**Non-Goals:**
- No audit-log table, no admin UI for events (log events only; the Loki
  pipeline from `observability` already collects them).
- No per-tenant spend cap: costs are estimates (CLAUDE.md), so a cap on money
  would act on invented values. Only a count cap on active experiments.
- No proxy-aware client address parsing (`X-Forwarded-For`); the
  address is `request.client.host`, documented for operators behind a proxy.

## Decisions

1. **`users.tokens_valid_after` (timestamptz, nullable).** `_resolve`
   rejects a token when `claims.issued_at < tokens_valid_after` (compared at
   second granularity, because `iat` has none) with reason `revoked`. It is set
   to `now()` on logout, password change and deactivation, in the transaction
   that does the change. Alternative: a `jti` deny list, rejected (grows,
   needs expiry cleanup). Consequence: logout ends the access tokens of all
   devices of that user (their refresh tokens of other logins keep working, so
   those clients simply refresh); documented. A token issued in the same
   second as the bump survives; accepted and documented.
2. **Refresh purge as CLI command** `python cli.py purge-refresh-tokens
   [--retention-days N]`, using `session_scope()`, plus an index on
   `refresh_tokens.expires_at`. No scheduler exists in the project; running
   it from cron/the operator is documented. Alternative: a Taskiq periodic task,
   rejected (no scheduler process today; adding one is a new moving part).
3. **Lock user row in refresh:** `UserRepository.get_for_update` (`SELECT ...
   FOR UPDATE`) before issuing the pair, also used by `update_user`. Lock order
   is always user row, then tokens, so no deadlock between refresh and update.
4. **Argon2 slot held until the thread ends.** `_run_bounded` starts the
   thread as a task, and if the awaiting coroutine is cancelled, releases the
   semaphore from the task's done-callback instead of in `finally`. The lazy
   dummy hash is `functools.cache`d.
5. **Leeway:** setting `jwt_leeway_seconds` (default 5, `ge=0, le=60`) passed
   as `leeway` to `jwt.decode`. Logout's `verify_expiry=False` path is unchanged.
6. **Double-submit refresh stays strict** (family revoked, as the spec
   says). The reason: a grace window weakens theft detection. Documented in
   docs/authentication.md, with advice for clients to serialize refreshes.
7. **Throttle in the database.** Table `auth_throttle(scope, key_hash,
   failures, window_started_at, locked_until)`, primary key (`scope`,
   `key_hash`); scopes `account` (SHA-256 of normalized email, so unknown
   emails are throttled identically and no raw email is stored) and `address`
   (hash of `request.client.host`). Settings: `auth_max_failed_attempts`
   (default 5, per account), `auth_max_failed_attempts_per_address` (default
   50, because many users can share one address), `auth_lockout_seconds`
   (default 900; also the counting window). The account counter is
   incremented atomically *before* the password check and committed before
   Argon2 starts (a locked key is refused without counting), and reset on
   success, so a parallel burst cannot verify more guesses than the
   threshold. The address counter (login, refresh, validate) is recorded
   after a failure with the same upsert and committed before the 401 is
   raised (persist-then-reject, as in refresh reuse), because the request
   fails. `/auth/validate` does not count `expired` tokens (normal gateway
   traffic), only `invalid` and `revoked`. `AuthThrottle` is a repository + small service in
   `components/auth`, built in `build_auth_service` via the existing
   constructor; the address is passed in by the endpoint (services never read
   HTTP). 429 with `Retry-After` comes from a new `AuthThrottledError`.
   Admin sign-in (`admin/users/admin.py::authenticate`) calls the same service
   with scope `admin`. Alternative: proxy-level limits only, rejected because
   they cannot key on the account and are not testable here; a proxy limit
   can still be added in front.
8. **Security events** use stdlib `logging` through one helper
   `log_security_event(name, **fields)` in `components/core/logging.py`
   (next to the JSON formatter). It takes only allowlisted fields (`user_id`,
   `client_address`, `outcome`, `reason`), formats values with `repr`-safe
   escaping, and never receives an email, token or body. Events:
   `auth.login_failed`, `auth.locked_out`, `auth.refresh_reuse`,
   `access.denied`, `admin.sign_in`. Access denials are logged in
   `components/core/access.py` where the denial is decided.
9. **Active-experiment cap** `max_active_experiments_per_organization`
   (default 20, `ge=1`), checked in `ExperimentService.start` against a count
   query (`running` status of the organization; `pending` experiments are created but not started, so they are not active, and `retry-failed` is checked the same way), answering a new
   `ExperimentQuotaError` mapped to 429. Count and start happen in one
   transaction after locking the organization row, so concurrent starts cannot
   overshoot.
10. **SAVEPOINTs:** `async with self.session.begin_nested()` around the
    insert/flush; `IntegrityError` is caught outside it. `OrganizationService`
    receives a `revoke_sessions: Callable[[uuid.UUID], Awaitable[int]]` port in
    its constructor (wired in `organizations/dependencies.py` from the auth
    repository), removing the import of `components.auth.repository`. A
    superadmin branch of `UserService.create_user` that always raises is
    simplified to one check; behavior and tests stay.
11. **Exception rename:** `InvalidTokenError`/`ExpiredTokenError` in
    `components/auth/exceptions.py` become `TokenInvalidError`/`TokenExpiredError`;
    every import is updated, no shim (CLAUDE.md).

## Risks / Trade-offs

- [Lockout of a victim by an attacker] -> per-account lock is capped at
  `auth_lockout_seconds`, correct logins from an unlocked address still reset
  nothing for a locked account; documented as the usual trade-off, tunable.
- [Throttle table growth] -> rows hold a hash and counters; the purge
  command also deletes rows whose `locked_until` and window ended.
- [Migration ordering with other changes] -> see Migration Plan.
- [Logout ends all access tokens of a user] -> documented; clients refresh.

## Migration Plan

Revision `0008` (`down_revision` `0007` while developing alone): add
`users.tokens_valid_after` (nullable, no backfill: null means never revoked),
index `ix_refresh_tokens_expires_at`, table `auth_throttle`. Downgrade drops
them; no result data is touched. Branches `tech-debt-experiment-pipeline`
(`0009`) and `tech-debt-deploy-and-migrations` (`0010`) rebase onto it: the
author of the later branch updates `down_revision` before merging, and the
single-head test of the deploy change catches a missed re-parenting.
Deploy order: migrate, then restart API and worker.
