# Proposal

## Why

Kan card LLM-9 (second half of the original users/organizations task). With identity in place (`user-authentication`), datasets and experiments must belong to users: a Staff user works only with their own datasets and experiments, an Admin sees their organization's, a SuperAdmin everything, and nobody can read or spend provider keys anonymously.

## What Changes

- Record the creating user and their organization (`owner_id`, `organization_id`) on every dataset and experiment.
- Scope all dataset, experiment, evaluation, metrics and export endpoints by role: Staff see only their own; Admin see their organization's; SuperAdmin see all. Resources outside a caller's scope respond exactly like missing ones (404).
- Require authentication on all of these endpoints. Health endpoints and the auth entry points stay public.
- Experiment creation requires a dataset visible to the caller.
- Add date range and status filters to dataset and experiment listings (and an owner filter for Admin and SuperAdmin).
- A SuperAdmin has no organization and therefore cannot create datasets or experiments.
- Migrate existing rows into a `Default` organization without an owner.
- **BREAKING**: every endpoint except health and the auth entry points now requires `Authorization: Bearer <access token>`; list responses change for non-privileged users.

## Capabilities

### New Capabilities
- `access-control`: required authentication, ownership recording, role-based visibility, hidden existence and legacy-data handling across all existing resources.

### Modified Capabilities
- `dataset-management`: created datasets record owner and organization; dataset listing is scoped and filterable by creation date.
- `experiment-execution`: experiment creation requires a visible dataset and records owner and organization; experiment listing is scoped and filterable by creation date and status.

## Impact

- `scope_clause` added to `components/core/access.py`; services, repositories and endpoints of datasets, experiments, evaluation and metrics take a `Principal` from `get_principal`. Background tasks are unchanged (internal, no user context).
- New columns `organization_id` and `owner_id` on `datasets` and `experiments`; new Alembic revision after `0005` with a data backfill and a `Default` organization.
- Most existing API tests are updated to authenticate with an authenticated client fixture.
- README updated; no new dependencies.
- Depends on `user-authentication` (organizations, users, tokens, `get_principal`).

## Assumptions

Recorded in design.md and revisable before apply: Staff own-only, Admin organization-wide and SuperAdmin platform-wide visibility, 404 instead of 403 for out-of-scope resources, SuperAdmin cannot create data (403), legacy rows unowned in the default organization visible to its Admins and SuperAdmins, and `created_from`/`created_to` as inclusive timezone-aware bounds.

## Out of Scope

- Sharing datasets or experiments between users, custom roles or permissions, an audit log.
- Deleting or transferring ownership of resources.
- Per-organization provider keys or quotas.
- Login rate limiting (a risk of `user-authentication`).
