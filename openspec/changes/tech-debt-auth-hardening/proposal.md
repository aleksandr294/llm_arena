# Proposal

## Why

The code reviews and the OWASP review recorded open auth findings (Kan cards
`g836fdnsjp10`, `m7edal84snn0`, `70bigdwxma03`): access tokens outlive logout
and password change, the credential endpoints have no throttling or lockout,
failed logins and permission denials leave no security trail, one tenant can
queue unlimited experiments, and the user/organization services roll back the
whole request transaction on a unique-key race. They all touch the same
files (`components/auth`, `components/users`, `components/organizations`),
so they are planned as one change.

## What Changes

- Access tokens are rejected after logout, password change and deactivation:
  `users.tokens_valid_after` is compared with the token `iat`.
- Refresh tokens are purged after expiry (CLI command and `expires_at` index);
  refresh locks the user row so deactivation cannot race an in-flight refresh.
- Argon2 slots are released only when the hash thread has finished, even if the
  caller is cancelled; the dummy hash is built lazily; `jwt.decode` accepts a
  configurable clock-skew leeway; double-submit refresh behavior is documented.
- Login, refresh, validate and admin sign-in are throttled by a database
  counter (per account and per client address) and answer 429 with
  `Retry-After` when locked out. **BREAKING** for clients that retry failed
  logins in a tight loop.
- Security events (failed login, lockout, refresh reuse, permission denial,
  admin panel sign-in) are written as structured log events without secrets.
- Experiments are limited per organization by a cap on active experiments.
- `UserService` / `OrganizationService` use `SAVEPOINT`s instead of
  `session.rollback()`, lock the row on update, and no longer import
  `components.auth`; dead superadmin branch removed; token exceptions renamed
  so they do not collide with PyJWT names.

## Capabilities

### New Capabilities
- `security-event-logging`: structured, secret-free log events for security
  relevant outcomes.

### Modified Capabilities
- `authentication`: token invalidation on session end, clock-skew leeway,
  refresh purge, login throttling.
- `organization-user-management`: failed writes keep earlier changes in the
  request; concurrent updates do not lose changes.
- `experiment-execution`: per-organization cap on active experiments.

## Impact

- Code: `components/auth/*`, `components/users/*`, `components/organizations/*`,
  `components/core/{config,database,schemas}.py`, `components/experiments/service.py`,
  `api/v1/endpoints/{auth,experiments}.py`, `api/v1/utils.py`, `admin/users/admin.py`, `cli.py`.
- Database: new migration (users.tokens_valid_after, refresh_tokens.expires_at
  index, auth_throttle table). It is the first of three migrations in this
  batch; see design.md for the merge order.
- Settings: `JWT_LEEWAY_SECONDS`, `AUTH_MAX_FAILED_ATTEMPTS`,
  `AUTH_LOCKOUT_SECONDS`, `MAX_ACTIVE_EXPERIMENTS_PER_ORGANIZATION`.
- Docs: `docs/authentication.md`, `docs/security.md`, `.env.example`.
- No new dependencies.
