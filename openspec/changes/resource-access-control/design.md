# Design

## Context

After `user-authentication` there are organizations, users, roles and a `get_principal` dependency returning `Principal(user_id, organization_id | None, role)`. Datasets, test cases, experiments, results, evaluations and metrics endpoints are still open; services take a session (or a session factory) and repositories have unscoped `list_page` queries. Background tasks run in workers without user context. See proposal.md for motivation and the spec and deltas.

## Goals / Non-Goals

**Goals:**
- Make it impossible to forget scoping: services require a `Principal`, repositories apply the scope.
- Preserve all existing data and leave workers untouched.
- Identical responses for missing and out-of-scope resources.

**Non-Goals:**
- Sharing, custom permissions, ownership transfer, audit trail (see proposal).

## Decisions

- **Columns and migration `0006`:** add `organization_id UUID NOT NULL FK organizations RESTRICT` and `owner_id UUID NULL FK users RESTRICT` to `datasets` and `experiments`, plus indexes on `(organization_id, created_at)` and `(owner_id, created_at)`. The migration inserts one `Default` organization, adds `organization_id` nullable, backfills every existing row with the default organization, then sets NOT NULL; `owner_id` stays null for legacy rows. Downgrade drops the columns, indexes and the default organization only if it has no users. Legacy rows are visible to Admins of the default organization and to SuperAdmins; Staff cannot see them. An operator creates an Admin for the default organization if the legacy data should be adopted.
- **Scoping helper:** `scope_clause(model, principal)` in `components/core/access.py` returns an SQLAlchemy condition: staff `owner_id == user_id`, admin `organization_id == org_id`, superadmin no condition. Services and repositories take a `Principal` and apply it in every read and in the fetch used by every write or action, so a scoped miss and a real miss are both `NotFoundError` and the endpoint answers an identical 404. Derived resources (cases, results, evaluations, summary, comparison, per-case view, export, evaluation backfill) first fetch the parent dataset or experiment with the scope clause.
- **Creation:** `DatasetService.create` and `ExperimentService.create` require `principal.organization_id` (SuperAdmin gets 403). The owner and organization always come from the principal, never the body (the schemas have no such fields and reject extra keys). Experiment creation fetches the dataset with the caller's scope (404 if outside); the experiment's `organization_id` is the dataset's and `owner_id` the caller's.
- **Listing and filters:** `created_from` and `created_to` (timezone-aware ISO 8601, naive values rejected with 422, from must not exceed to, both inclusive), `status` for experiments (enum), `owner_id` for Admin and SuperAdmin (for Staff the scope already restricts to own, so it cannot widen results). Ordering `(created_at, id)` oldest first stays; totals reflect filters and scope.
- **Workers and tasks:** generation and evaluation tasks keep calling repositories without a principal (internal paths use explicitly unscoped repository methods named as such), so no worker behavior changes.
- **Endpoints:** every existing router in `api/v1/endpoints/{datasets,experiments,evaluations,metrics}.py` depends on `get_principal`; health and auth routers do not. Metrics and export streaming resolve scope before the response starts, so an out-of-scope export streams nothing.
- **Errors:** 401 without a valid token, 404 for out-of-scope resources, 403 only for the SuperAdmin creation case, 422 for bad filters.
- **Logging:** user id, endpoint and outcome category; no tokens or resource content.
- **Tests:** a shared authenticated-client fixture (staff A, staff B, admin same organization, admin other organization, superadmin) used by all API tests; per-service scope tests; a role by resource by endpoint isolation matrix over every protected endpoint; migration upgrade/downgrade tests on a database with existing rows; log-leak tests.
- **Documentation:** README updated for visibility rules, filters, the breaking change and migration steps.

## Risks / Trade-offs

- [Breaking change for existing clients and many tests] → documented as BREAKING; tests get one auth fixture; clients must log in.
- [A forgotten unscoped query leaks data] → `Principal` is a required argument of service methods and the isolation matrix covers every protected endpoint.
- [404 hides existence but could leak by timing] → both paths run the same scoped query.
- [Legacy data unreachable for Staff] → intended; an Admin of the default organization or a SuperAdmin can see it.
- [Cases are append-only but owned by the dataset] → inherit the dataset scope; no per-case owner.
- [`experiments.organization_id` equals the dataset's organization only by application logic, not a database constraint] → enforced in the experiment service on creation; direct SQL writes can break it.

## Migration Plan

Deploy after `user-authentication` is live: `alembic upgrade head` (0006), then create an Admin for the `Default` organization if legacy data should be adopted. Rollback: `alembic downgrade 0005` removes the columns (ownership information is lost; datasets and experiments are kept).

## Open Questions

- Whether SuperAdmin should be able to create data on behalf of an organization later (currently 403); deferred.
