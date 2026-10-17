# Proposal

## Why

Kan card LLM-8 (first half of the original users/organizations task). The platform has no notion of who is calling. Before data can be scoped per user, it needs organizations, users in three roles and JWT authentication that is safe to rely on. This change delivers that identity foundation without touching existing endpoints; the follow-up change `resource-access-control` (Kan card LLM-9) uses it to protect datasets and experiments.

## What Changes

- Add organizations and users. Roles: `staff`, `admin`, `superadmin`. Staff and Admin belong to one organization; SuperAdmin is platform-level and belongs to none.
- Add JWT authentication: login, token validation, refresh with rotation and reuse detection, and logout. Access tokens are short-lived; refresh tokens are stored server-side (hashed) so they can be revoked.
- Add management endpoints: SuperAdmin manages organizations; Admin manages users of their own organization; SuperAdmin manages users of any organization.
- Add a separate Typer-based `cli.py` module for operator commands, starting with `create-superadmin` to bootstrap the first SuperAdmin. `main.py` and `python main.py API` stay unchanged.
- Add a `get_principal` dependency that resolves the authenticated user (database-authoritative) for use by this change's endpoints and by `resource-access-control`.
- Existing dataset, experiment, evaluation and metrics endpoints are not changed and remain open until `resource-access-control` lands. A new required `JWT_SECRET_KEY` setting is added.

## Capabilities

### New Capabilities
- `authentication`: login, JWT access and refresh tokens, validation, rotation, revocation and secure credential handling.
- `organization-user-management`: roles, organizations, users and the bootstrap of the first SuperAdmin.

### Modified Capabilities

## Impact

- New components `components/organizations/`, `components/users/` and `components/auth/` (models, schemas, repositories, services, security helpers), endpoints `api/v1/endpoints/{auth,organizations,users}.py`, `cli.py`, and `Role` and `Principal` in `components/core/access.py`.
- New tables `organizations`, `users`, `refresh_tokens`; new Alembic revision after `0004`. No change to existing tables.
- New dependencies: `PyJWT` (token signing and validation), `argon2-cffi` (Argon2id password hashing) and `typer` (command-line interface).
- `components/core/config.py`, `.env.example`, README and the CLAUDE.md structure example updated.
- Built on top of the `metrics-and-comparison` branch, which main does not contain yet.

## Assumptions

Recorded in design.md and revisable before apply: role semantics (Staff own data, Admin organization, SuperAdmin platform), access token 15 minutes and refresh token 7 days, user state read from the database on every request, the first SuperAdmin created by a command rather than an open endpoint, SuperAdmin never created or edited over HTTP.

## Out of Scope

- Scoping of datasets, experiments and derived data (next change, `resource-access-control`).
- Self-service sign-up, email verification, password reset by email, self-service password change, multi-factor authentication, OAuth or SSO.
- Login rate limiting or lockout (noted as a risk), CORS configuration, API keys for machine clients.
- Custom roles or permissions, audit log, deleting users or organizations (deactivation only), an admin UI (separate card).
