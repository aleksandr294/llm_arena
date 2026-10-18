# Proposal

## Why

Kan card LLM-14 ("Добавить Админку и Swagger"). People who run the platform need a browsable interface to see datasets, experiments and their results, organizations and users without calling the REST API by hand, with exactly the same visibility rules as the API (Staff their own, Admin their organization, SuperAdmin everything), and SuperAdmins must be able to block and unblock organizations. Developers also need a good interactive REST documentation (Swagger) that explains authentication and every endpoint.

## What Changes

- Add an admin panel built on the `fastadmin` package, mounted at `/admin`, optional (mounted only when an admin secret is configured), signing in with the same email and password as the REST API.
- Show datasets, test cases, experiments, experiment results, evaluation results, users and organizations in the panel. Everything is read-only except one action: a SuperAdmin can block and unblock an organization.
- Enforce the access rules on every admin query at one choke point, so Staff see only their own data, Admin their organization's, SuperAdmin all, and a missing or inactive user sees nothing.
- Hide password hashes and bulky text columns, and disable data export in the panel (the REST export endpoint remains the supported route).
- Blocking an organization in the panel behaves exactly like deactivating it through the REST API: its users can no longer sign in and their refresh tokens are revoked.
- Improve the REST documentation served by FastAPI (Swagger UI at `/docs`, ReDoc at `/redoc`, OpenAPI at `/openapi.json`): metadata, tag descriptions, summaries, documented error responses, a working "Authorize" button for Bearer tokens, and a setting to switch the documentation off.

## Capabilities

### New Capabilities
- `admin-panel`: the role-scoped, mostly read-only administration UI with organization blocking.
- `api-documentation`: the interactive, authenticated REST API documentation.

### Modified Capabilities

## Impact

- New top-level package `admin/` (per the structure in the card): a mount function, a scoped base admin class, and per-component admin classes; mounted from `api/app.py`.
- New dependency `fastadmin` (extras `fastapi` and `sqlalchemy`; it brings `python-multipart` and `asgiref`). It reads its configuration from environment variables at import time and ships a prebuilt frontend bundle.
- New optional settings: `ADMIN_SECRET_KEY`, `ADMIN_SESSION_COOKIE_SECURE`, `API_DOCS_ENABLED`; `.env.example`, README and the CLAUDE.md structure example updated.
- No database migration. Existing REST behavior is unchanged.
- Built on `main`, which already contains users, organizations and access control.

## Assumptions

Recorded in design.md and revisable before apply: the panel is read-only apart from blocking organizations (the card asks for viewing and for blocking), staff may sign in and see only their own data, export is disabled, the panel is off unless `ADMIN_SECRET_KEY` is set, the admin session is separate from the REST JWT and has its own secret, and Swagger already exists in FastAPI so the work is documentation quality and an off switch rather than a new UI.

## Out of Scope

- Creating, editing or deleting datasets, experiments, results, users or organizations in the panel (the REST API remains the way to do that), and starting experiments from the panel.
- Custom dashboards or charts, a bespoke frontend, multi-language customization, theming.
- Rate limiting of admin sign-in (same known gap as REST login).
- Single sign-on between the REST API and the panel.
