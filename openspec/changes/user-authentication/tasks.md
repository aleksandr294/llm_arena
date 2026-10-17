# Tasks

## 1. Foundations

- [x] 1.1 Add `PyJWT`, `argon2-cffi` and `typer` with Poetry (update the lock file), add settings `jwt_secret_key` (required SecretStr, at least 32 characters, rejected if it equals the example placeholder), `jwt_access_ttl_seconds`, `jwt_refresh_ttl_seconds`, `jwt_issuer` to `components/core/config.py` and `.env.example` (placeholder only), update existing test fixtures that build settings, and verify `poetry install`, config unit tests for every invalid secret case, and that `tests/test_config.py` passes
- [x] 1.2 Add `components/core/access.py` (`Role`, `Principal`) and `components/auth/security.py` (Argon2id password hashing in a thread with rehash-on-login via `check_needs_rehash`, verify with a dummy hash for unknown users, password policy, JWT encode/decode with pinned algorithm and required claims), and verify unit tests for hash uniqueness and verification, wrong password, rehash of an outdated hash, policy limits, token expiry, wrong issuer, tampering, algorithm none and wrong token type

## 2. Persistence

- [x] 2.1 Add `components/organizations/models.py`, `components/users/models.py` and `components/auth/models.py` (`Organization`, `User` with the role/organization CHECK, `RefreshToken`) with repositories, register them in `migrations/env.py`, and verify integration tests for uniqueness ignoring case, the CHECK constraint, cascade rules and token lookup by hash
- [x] 2.2 Generate and hand-review revision `0005` creating the three tables and indexes, and verify `alembic upgrade head` and `downgrade 0004` succeed on a clean database and `alembic check` shows no drift

## 3. Authentication

- [x] 3.1 Add `AuthService` (login with uniform errors, access/refresh issuing, refresh rotation, reuse detection revoking the family, logout, validation with reasons, session revocation on deactivation or password change) and verify integration tests for every authentication spec scenario including case-insensitive email, deactivated user and organization, expired and reused refresh tokens
- [x] 3.2 Add `api/v1/endpoints/auth.py` (`POST /auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/validate`), the `get_principal` dependency in `api/v1/utils.py` with database-authoritative user state and `WWW-Authenticate: Bearer`, register the router, and verify API tests for 200/401/422, the uniform failure response, bearer parsing errors and that a deactivated user's token stops working immediately
- [x] 3.3 Add `cli.py` (Typer app) with `create-superadmin --email <email>` (hidden double prompt or `SUPERADMIN_PASSWORD`, same policy, refuses duplicates, prints only the id, password never a CLI argument) leaving `main.py` untouched, and verify tests with Typer's `CliRunner` for success, duplicate email, weak password, password not echoed or logged, `python main.py API` still dispatching as before, and that no HTTP endpoint creates the first user

## 4. Organization and user management

- [x] 4.1 Add `OrganizationService` and `api/v1/endpoints/organizations.py` (create, list, get, update name and active state; superadmin only), and verify API tests for 201/403/404/409 (duplicate name ignoring case)/422, pagination, and that deactivating an organization ends its users' sessions
- [x] 4.2 Add `UserService` and `api/v1/endpoints/users.py` (create, list with organization/role/active filters and pagination, get, update role/active/password) enforcing the role rules (admin limited to own organization and to staff/admin, no superadmin via HTTP, no self role change or self deactivation), and verify API tests for every role-matrix scenario in the spec, password policy errors, duplicate email, never returning hashes and session revocation on deactivate and password change

## 5. Documentation and final checks

- [x] 5.1 Document in README.md and in the CLAUDE.md project-structure example (add `cli.py`) the roles, the bootstrap command `python cli.py create-superadmin`, the login/refresh/logout/validate flow with `curl` examples, organization and user management, the new required settings and the migration steps, and verify the documented commands work against a running API with a scratch database
- [x] 5.2 Add a test that tokens, passwords and hashes never appear in captured logs during login, refresh, failed attempts and management calls, then run `pytest` (including integration tests), `ruff check .`, `black --check .` and `mypy` and verify all pass, noting any tests that could not run

## Workflow follow-up

- Move Kan card LLM-8 (`5j1uqfndnfmp`) to In-progress when work starts and to Review once the reviewer agent has reviewed it (see the `kan-review` skill).
- Work on branch `feat/user-authentication` (stacked on `feat/metrics-and-comparison`), commit with the `git-conventional-commits` skill, do not push without being asked, and archive the change after approval.
