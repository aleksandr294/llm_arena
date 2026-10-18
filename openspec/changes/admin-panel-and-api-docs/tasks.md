# Tasks

## 1. Dependency and configuration

- [x] 1.1 Add `fastadmin` (extras `fastapi`, `sqlalchemy`, pinned to the inspected 0.10 series) with Poetry, add optional settings `admin_secret_key` (at least 32 characters, must differ from `jwt_secret_key`), `admin_session_cookie_secure` and `api_docs_enabled` to `components/core/config.py` and `.env.example`, and verify `poetry install`, config unit tests for weak, equal-to-JWT and blank admin secrets (errors never echo values) and that `tests/test_config.py` passes

## 2. Scoped admin foundation

- [x] 2.1 Add `admin/core.py` with the scoped sessionmaker (principal in `session.info`, `do_orm_execute` + `with_loader_criteria` for Dataset, TestCase, Experiment, ExperimentResult, EvaluationResult, User, Organization, fail closed without an active principal) and `ReadOnlyScopedAdmin` (no add/change/delete/export/action), and verify integration tests for every role on every scoped model: staff own, admin organization, superadmin all, children through parents, users and organizations rules, legacy rows, and no rows without a principal
- [x] 2.2 Add `admin/__init__.py` `mount_admin` (set fastadmin environment from settings before the first lazy import, register all admin classes, mount at `/admin`, skip when `admin_secret_key` is unset) and call it from `api/app.py`, and verify tests that the app starts without the secret with `/admin` answering 404, that startup fails for an invalid admin secret, and that mounting does not add admin paths to the main OpenAPI schema

## 3. Model admins

- [x] 3.1 Add the User admin (email as username, `authenticate` through the Argon2id helpers with the dummy-hash path, `get_obj` returning `None` for inactive users or organizations, binding the principal, `exclude` of the password hash, no password change) and the read-only admins for datasets, test cases, experiments, experiment results and evaluation results with list columns that omit bulky text, and verify tests driving fastadmin's HTTP API with cookies for sign-in as staff, admin and superadmin, generic failure for wrong password, unknown email, deactivated user and deactivated organization, session invalidation after deactivation, scoped lists and gets per role, hash absent in every response, and add/change/delete/export answering 403
- [x] 3.2 Add the Organization admin (superadmin only, `is_active` only, other fields ignored server-side, delegating to `OrganizationService.update` so refresh tokens are revoked in the same transaction, no add or delete), and verify tests that a superadmin blocks and unblocks an organization with the REST login and refresh stopping and resuming, that other fields in the payload are ignored, that admin and staff get 403, and that a user's panel session returns 401 after their organization is blocked

## 4. API documentation

- [x] 4.1 Update `create_app` and the routers with title, version, description (authentication, roles, visibility), `openapi_tags` descriptions, per-operation summaries, shared 401/403/404/409/422 `responses` with one error body schema, `persistAuthorization`, and the `api_docs_enabled` switch, and verify tests on the generated OpenAPI that every operation has a tag and a summary, that protected operations declare the Bearer requirement and public ones do not, that error responses are listed, that no admin path appears, and that disabling the switch makes `/docs`, `/redoc` and `/openapi.json` answer 404 while the API keeps working

## 5. Cross-cutting checks and documentation

- [x] 5.1 Extend the route-coverage test with the admin data routes (every `/admin/api/*` data route needs a valid session) and add a log-leak test that passwords, session cookies, hashes and row content never appear in captured DEBUG logs while signing in, failing to sign in and browsing, and verify both pass
- [x] 5.2 Document in README.md and in the CLAUDE.md project-structure example (add `admin/`) how to enable the panel, the new settings and cookie note, what each role sees, the read-only rule, organization blocking, the Swagger usage with the Authorize button and the documentation switch, and verify the documented steps work against a running API with a scratch database (panel sign-in and a superadmin block through the browser API calls, `/docs` and `/openapi.json`)
- [x] 5.3 Run `pytest` (including integration tests), `ruff check .`, `black --check .` and `mypy` and verify all pass, noting any tests that could not run

## Workflow follow-up

- Move Kan card LLM-14 (`q6vscx09ifgw`) to In-progress when work starts and to Review once the reviewer agent has reviewed it (see the `kan-review` skill).
- Work on branch `feat/admin-panel-and-api-docs` (from `main`), commit with the `git-conventional-commits` skill, do not push without being asked, and archive the change after approval.
