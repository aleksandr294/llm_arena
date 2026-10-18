# Design

## Context

Users, organizations, JWT auth (`get_principal`, Argon2id) and role-based access (`scope_clause`, `Role`, `Principal`) exist on `main`. `fastadmin` 0.10.0 (MIT, Python >= 3.12, extras `fastapi` and `sqlalchemy`) was inspected before planning: it exposes a self-contained FastAPI sub-app (`fastadmin.fastapi_app`) with its own `/api/*` routes and a prebuilt frontend; model admins subclass `SqlAlchemyModelAdmin` and are registered with `@register(Model, sqlalchemy_sessionmaker=...)`. Facts that shape the design:
- Sign-in calls `authenticate(username, password)` on the admin registered for `ADMIN_USER_MODEL`; the session is its own JWT in an HttpOnly cookie signed with `ADMIN_SECRET_KEY`. Every request re-validates the session through the user admin's `get_obj(user_id)` and then binds `request` and `user` to each model admin (`set_context`).
- It has no row-level scoping. Queries go through `get_sessionmaker()`; lists, gets, saves and deletes use `session.get`, `select` and `session.delete` on that sessionmaker.
- Permission hooks `has_add/change/delete/export_permission(user_id)` are enforced server-side on mutating endpoints.
- Configuration is read from `os.environ` once at import time (`ADMIN_SECRET_KEY`, `ADMIN_USER_MODEL`, cookie flags, prefix).
- FastAPI already serves Swagger UI, ReDoc and the OpenAPI schema by default.
See the proposal for motivation and the specs for behavior.

## Goals / Non-Goals

**Goals:**
- Reuse the REST visibility rules in the panel from a single enforcement point, so a new panel model cannot forget scoping.
- Keep the panel inert unless explicitly configured, with its own secret.
- Treat blocking an organization in the panel as the same domain operation as in REST.

**Non-Goals:**
- Editing business data in the panel, dashboards, SSO, rate limiting (see proposal).

## Decisions

- **Layout (per the card):** top-level `admin/` package: `admin/__init__.py` exposes `mount_admin(app, settings)`; `admin/core.py` holds the scoped session machinery and `ReadOnlyScopedAdmin`; `admin/<component>/admin.py` registers the model admins for datasets (Dataset, TestCase), experiments (Experiment, ExperimentResult), evaluation (EvaluationResult), users and organizations. `api/app.py` calls `mount_admin` only when `settings.admin_secret_key` is set.
- **Configuration order:** because fastadmin reads the environment at import, `mount_admin` sets the needed variables from our settings (`ADMIN_SECRET_KEY`, `ADMIN_USER_MODEL=User`, `ADMIN_USER_MODEL_USERNAME_FIELD=email`, `ADMIN_SESSION_COOKIE_SECURE`) and only then imports `fastadmin`, lazily and once. The panel is skipped (with an info log) when no admin secret is set, so nothing imports it in that case. New settings: `admin_secret_key` (SecretStr, optional, at least 32 characters, must differ from `jwt_secret_key`), `admin_session_cookie_secure` (default true; README tells local HTTP users to set false), `admin_session_ttl_seconds` (default 28800, at least 60; exported as `ADMIN_SESSION_EXPIRED_AT`; `ADMIN_SESSION_COOKIE_SAMESITE` is pinned to `lax`), `api_docs_enabled` (default true).
- **Single scoping choke point:** each admin class gets its sessions from a scoped sessionmaker. Sessions carry the authenticated principal in `session.info`; a `do_orm_execute` event adds `with_loader_criteria` for every scoped model (`include_aliases=True`) so selects, `session.get`, counts and relationship loads are all filtered by the same `scope_clause` logic as REST. Child models resolve their scope through their parent: TestCase through `Dataset`, ExperimentResult and EvaluationResult through `Experiment` (subquery criteria on the parent's scope). A session without a principal (or with an inactive one) gets a false criterion for every scoped model, so it fails closed. Alternative rejected: overriding each `orm_*` method (copies library query code, easy to miss one, breaks on upgrades).
- **User model and authentication:** the User admin implements `authenticate` by looking up the active user (case-insensitive email) and verifying the password with the existing Argon2id helpers (`components/auth/security.py`, including the dummy-hash path for unknown emails), returning the user UUID only for active users of active organizations. It overrides `get_obj` (called by fastadmin on every request) to use an unscoped read of exactly the session's user and to return `None` when the user or organization is inactive, which makes the session invalid immediately (401). The session-validating call returns a private `SessionUser` wrapper (never a serialisable value) around `Principal(user_id, organization_id, role)`; data routes whose bound user is missing (fastadmin's second lookup came back empty) answer 404 instead of falling back to the unscoped read. The wrapper is bound to the scoped sessionmaker via `set_context`. `change_password` raises (no password changes in the panel).
- **Role visibility mapping:** Dataset/Experiment: `scope_clause` (staff owner, admin organization, superadmin all). User: staff self, admin own organization (never superadmins), superadmin all. Organization: superadmin all, everyone else a false criterion. Legacy rows follow the existing rules (owner NULL: invisible to staff).
- **Read-only:** a base `ReadOnlyScopedAdmin` returns False from `has_add/change/delete/export/action_permission`, which fastadmin enforces server-side. Export is off deliberately: fastadmin 0.10 neutralises CSV formulas itself (`_neutralize_csv_value`), but its export builds the columns from all model fields regardless of `exclude`, so it would bypass the hidden-column rules and dump data in bulk. The scoped REST export endpoint stays the supported way.
- **Organizations block/unblock:** `OrganizationAdmin` allows `change` only for superadmin, restricts editable fields to `is_active` (all others read-only and ignored server-side by overriding `save_model` to take only `is_active` from the payload), has no add/delete, and calls `OrganizationService.update` so session revocation happens in the same transaction as in REST. A non-superadmin gets 403 from `has_change_permission`.
- **Hidden and bulky columns:** `exclude = ("password_hash",)` on User (and not registering `RefreshToken` at all); `list_display` omits `response_text`, `rendered_prompt`, `input`, `expected_output`, `config`/`evaluator_config` JSON; these appear only in detail views. Row display strings (`__str__`) are overridden to the model name plus a short id so the default object representation (module path, memory address) is never serialised. List `limit` is capped by fastadmin's `ADMIN_QUERY_MAX_LIMIT` (1000); we also lower `list_per_page` to a sensible default.
- **Request logging:** no custom logging of credentials, cookies or row content; the app-level exception handler of fastadmin logs only exception text, which is checked by a test with sentinel values.
- **API documentation:** `create_app` sets `title`, `version`, a `description` (authentication flow, roles, visibility), `openapi_tags` with descriptions, `docs_url`, `redoc_url`, `openapi_url` (all `None` when `api_docs_enabled` is false) and `swagger_ui_parameters={"persistAuthorization": True}`. Each router gets `tags`, each operation a `summary`, and shared `responses` entries (401, 403, 404, 409, 422 with one `ErrorSchema` body) so the schema documents errors consistently without changing runtime behavior. The existing `HTTPBearer` dependency already declares the Bearer scheme; public routes simply do not depend on it, which keeps their `security` empty. The mounted admin app is not part of the main app's OpenAPI schema because it is a mounted sub-application with `openapi_url=None`.
- **Tests:** admin tests drive fastadmin's own HTTP API through the mounted app with cookies: sign-in for the three roles and failures, session invalidation after deactivation or blocking, scoped list/get per model and role (including legacy rows and cross-organization ids), read-only enforcement (add/change/delete/export all 403), password hash absent, block/unblock with session revocation, fail-closed without a user, log-leak test. The existing route-coverage test gets an explicit admin case (every `/admin/api/*` data route needs a session). OpenAPI tests assert tags, summaries, security and error responses and that the switch returns 404s.

## Risks / Trade-offs

- [Third-party admin app is a second API surface and ships a prebuilt JS bundle] → pin `fastadmin` to the inspected minor version, rely on `poetry.lock` hashes, keep the panel off by default, make it read-only and scope every query centrally.
- [`with_loader_criteria` and library internals may change] → pin the version; tests exercise real fastadmin endpoints per role so an upgrade that bypasses scoping fails loudly.
- [Panel sign-out only clears the browser cookie; the signed value stays valid until it expires] → short configurable TTL (8 hours by default); deactivating the user or organization ends the session at once; rotating `ADMIN_SECRET_KEY` ends all sessions.
- [fastadmin's own export ignores hidden columns] → export permission is denied for every model admin.
- [Driver errors quote filter values and fastadmin logs exception text] → engine `hide_parameters=True`, and filter values are coerced to the column type in `ReadOnlyScopedAdmin.orm_get_list`, answering 422 before the database is reached.
- [Swagger UI/ReDoc load assets from a CDN] → FastAPI default; `API_DOCS_ENABLED=false` for production exposure (documented).
- [Separate session and secret from REST JWTs] → intentional; tradeoff is one more secret to manage. Rotating `ADMIN_SECRET_KEY` ends all panel sessions.
- [No sign-in rate limiting] → same known gap as REST login; to be addressed for both.
- [Panel cookies require HTTPS by default] → documented `ADMIN_SESSION_COOKIE_SECURE=false` for local HTTP only.
- [Hidden columns visible on detail views of rows the user may read] → within scope and by design (the owner can already read them through REST).
- [Environment mutation at mount time] → done once, before the first fastadmin import, and tested; tests that mount the panel run in a subprocess or reset the module registry.

## Migration Plan

No database change. To enable: generate `ADMIN_SECRET_KEY` (different from `JWT_SECRET_KEY`), set it in `.env`, restart the API; open `/admin` and sign in with an existing REST user. Leaving it unset keeps the panel off. Rollback: unset the variable or revert the change; nothing persists.

## Open Questions

- Whether staff should be allowed into the panel at all (current assumption: yes, own data only); revisit if the REST API is considered enough for them.
