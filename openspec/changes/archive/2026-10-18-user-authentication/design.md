# Design

## Context

No authentication exists. Settings use `pydantic-settings` with `SecretStr`. `main.py` is an argparse entry point whose `API` command is required by the service-runtime spec. Workers do not need user context. See proposal.md for motivation and the two new specs. The change is stacked on the `metrics-and-comparison` branch only because main does not contain it yet; nothing here depends on its code.

## Goals / Non-Goals

**Goals:**
- Authoritative, immediately revocable sessions; no trust in stale token claims.
- Standard, well-reviewed cryptographic libraries instead of hand-rolled code.
- A reusable `Principal` and `get_principal` dependency for the next change.
- No change to existing endpoints or tables.

**Non-Goals:**
- Resource scoping, sign-up, password reset, MFA, SSO, rate limiting, custom roles, audit log, UI (see proposal).

## Decisions

- **Layout:** `components/organizations/`, `components/users/`, `components/auth/` each with `models.py`, `schemas.py`, `repository.py`, `service.py` as needed (auth adds `security.py`), plus `components/core/access.py` for `Role` and `Principal`. Endpoints in `api/v1/endpoints/{auth,organizations,users}.py`; the shared `get_principal` dependency lives in `api/v1/utils.py`; operator commands in a new top-level `cli.py`.
- **Tables:**
  - `organizations(id UUID, name varchar(255), name_lower unique, is_active bool, created_at)`.
  - `users(id UUID, organization_id FK RESTRICT null, email, email_lower unique, password_hash text, role varchar(16), is_active bool, created_at)` with `CHECK ((role = 'superadmin') = (organization_id IS NULL))`.
  - `refresh_tokens(id UUID pk, user_id FK CASCADE, family_id UUID, token_hash char(64) unique, expires_at, used_at null, revoked_at null, created_at)` with indexes on `family_id` and `user_id`.
  Roles and states are lowercase varchar like the rest of the schema. Revision `0005` creates the three tables; downgrade drops them.
- **Passwords:** `argon2-cffi` with Argon2id (`PasswordHasher` using the library's recommended parameters), the OWASP-preferred algorithm. The encoded hash string carries salt and parameters, verification is constant-time inside the library, and `check_needs_rehash` is used on a successful login to transparently upgrade hashes when parameters are raised. Hashing and verification run in a worker thread (`asyncio.to_thread`) so they do not block the event loop. For unknown emails a precomputed dummy hash is verified to keep timing uniform. Alternatives: stdlib `hashlib.scrypt` (rejected: acceptable but second choice, custom encoding and rehash logic to maintain) and `bcrypt` (rejected: 72-byte password limit and weaker against GPU attacks).
- **JWT:** `PyJWT` (justified: signing and claim validation are security critical and must not be hand-written), algorithm pinned to HS256 with an explicit `algorithms=["HS256"]` list, required claims `exp`, `iat`, `iss`, `sub`, `jti`, `typ`; access `typ=access` 15 minutes, refresh `typ=refresh` 7 days. Settings: `JWT_SECRET_KEY` (SecretStr, required, at least 32 characters, rejected if it equals the example placeholder), `JWT_ACCESS_TTL_SECONDS`, `JWT_REFRESH_TTL_SECONDS`, `JWT_ISSUER`.
- **Refresh tokens:** the refresh JWT carries `jti` and `fam`; the server stores `sha256(jti)` with `family_id`. Refresh = verify JWT, look up by hash, require not expired, not revoked and not used, mark used, issue a new pair in the same family. A presented used token revokes the whole family and answers 401. Logout revokes the family. Deactivating a user or organization, or changing a password, revokes all of the user's refresh tokens; the database-authoritative principal check below also cuts off access tokens immediately.
- **Principal resolution:** `get_principal` decodes the bearer token, then loads the user and organization from the database on each request and rejects inactive users or organizations; role and organization come from the database, not from claims, so role changes and deactivation take effect at once (one indexed lookup per request). Returns `Principal(user_id, organization_id | None, role)` or a 401 with `WWW-Authenticate: Bearer`.
- **Admin rules:** admins cannot touch other organizations, cannot create or modify superadmins, cannot change their own role or deactivate themselves; superadmin cannot be created or edited through HTTP. These checks live in `UserService`, not in handlers.
- **CLI module (`cli.py`, Typer):** a new top-level `cli.py` holds a Typer app for operator tasks; `main.py` keeps running only the API server with its existing `API` command, so the service-runtime spec, the Dockerfile and the README command are untouched. The first command is `python cli.py create-superadmin --email <email>`: it prompts for the password twice with hidden input (`typer.prompt(hide_input=True, confirmation_prompt=True)`) or reads it from the `SUPERADMIN_PASSWORD` environment variable for non-interactive use, applies the same password policy, refuses duplicates and prints only the user id. The password is never an argument, so it does not appear in process lists or shell history. Command logic lives in the users service; the Typer function only parses input, opens a short-lived session and runs the async service through `asyncio.run`. Alternatives rejected: migrating `main.py` to Typer (needlessly touches the API entry point and its spec) and an HTTP bootstrap endpoint (unauthenticated user creation).
- **Errors:** 401 for authentication problems, 403 for role violations on management endpoints, 409 for conflicts (duplicate email or name, self lockout), 422 for validation. Error bodies never include tokens, hashes or submitted passwords.
- **Logging:** user id, failure category and endpoint only; no tokens, passwords, hashes or unknown emails.
- **Tests:** unit tests for password hashing and JWT (expiry, issuer, alg none, wrong type, tampering, rehash); integration tests for repositories, the migration, the auth flows (rotation, reuse detection, logout, deactivation), user and organization rules, and `CliRunner` tests for the command.

## Risks / Trade-offs

- [Login brute force] → no rate limiting or lockout in this change; mitigated by memory-hard hashing and uniform errors; follow-up needed before public exposure.
- [One DB lookup per request] → accepted for immediate revocation; indexed by primary key.
- [Shared secret HS256] → a leaked secret forges tokens; rotate by changing the secret (invalidates all sessions); asymmetric keys are a later option.
- [Argon2 cost on the event loop and memory use under many parallel logins] → hashing runs in a worker thread; the server limits concurrency; login rate limiting is the follow-up.
- [Refresh race] → a legitimate client retrying a refresh after a lost response is treated as token reuse and loses the whole login session; no grace window by design.
- [Access token lifetime] → access tokens stay valid up to their 15-minute lifetime after a password change or logout unless the user or organization is deactivated (re-checked per request); refresh tokens are revoked immediately.
- [Unused identity until the next change] → intended; this change is additive and safe to ship alone.

## Migration Plan

Set `JWT_SECRET_KEY`, run `alembic upgrade head` (0005), run `python cli.py create-superadmin --email <email>`, then create organizations and admins. Rollback: `alembic downgrade 0004` drops the three tables (identity data is lost; nothing else is affected).
