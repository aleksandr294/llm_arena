# Design

## Context

`admin/core.py` defines `ReadOnlyScopedAdmin`: scoped sessions, all `has_*`
permissions false, and `save_model`/`delete_model` effectively blocked by
those permissions. Only `OrganizationAdmin` opens `has_change_permission` and
routes `save_model` through `OrganizationService.update` in a `session_scope`.
Business rules already live in `UserService` (create/update with caller
`Principal`), `OrganizationService`, `AuthService.revoke_all_sessions` and the
cascade foreign keys of results and evaluations. `ExperimentRepository.delete`
is deliberately disabled and no service deletes experiments.

## Goals / Non-Goals

**Goals:**
- Reuse the REST services so panel and API rules cannot diverge.
- Keep deny-by-default: each capability is opened per admin class and per role.

**Non-Goals:**
- Editing profile fields, deleting users/organizations/datasets, REST delete
  endpoint for experiments, changing passwords, export.

## Decisions

1. **Per-class opt-in on top of the read-only base.** Each admin class
   overrides `has_add/change/delete/action_permission` using the principal
   (`principal_of(self.user)`) and its own `save_model`/`delete_model`, which
   take only whitelisted payload keys and call a service. Alternative: loosen
   the base class and subtract; rejected, it fails open for new models.

2. **Services take the caller `Principal`.** User block and create call
   `UserService.update_user(caller, id, is_active=...)` and `create_user`;
   organization creation calls `OrganizationService.create`. Their errors map
   to `AdminApiException` (403 forbidden, 404 hidden, 409/422 input) with
   messages that never echo passwords. Each runs in one `session_scope()`
   (one operation, one commit) using `build_*` factories, as the organization
   admin already does.

3. **User password and organization on create.** fastadmin builds the add
   form from model columns. Exposing the real `password_hash` column would make
   the hash filterable and sortable, so instead the add form gets synthetic
   form-only fields: `password` (password widget, never serialized, not
   writable, filter/sort refused) and `organization_id` (required for
   superadmins, optional for admins who may only name their own). `save_model`
   hands them to `UserService.create_user`, which hashes the password. A
   crafted `password_hash` key is ignored.

4. **Revoke tokens as a bulk action** (`actions = ("revoke_refresh_tokens",)`)
   on `UserAdmin`, gated by `has_action_permission` for admin/superadmin. The
   scoped session filters the selected ids first, so out-of-scope ids are
   silently skipped, then `AuthService.revoke_all_sessions` per id. Needs
   `build_auth_service(session, settings)`; settings are injected into the
   admin through the existing entry-point wiring, not created in the service.

5. **Experiment delete.** New `ExperimentService.delete(principal, id)`:
   lock the row with the existing `_locked`, refuse non-terminal status with
   `InvalidTransitionError(..., "delete")`, delete via a new scoped repository
   method (the inherited `delete` stays disabled), rely on `ON DELETE CASCADE`
   for results and evaluations. fastadmin turns every `delete_model` error into a 500, so
   `ExperimentAdmin.has_delete_permission` pre-checks the experiment (404 if not
   visible, 409 if active, 403 for staff) and the service stays authoritative. The terminal check and delete share one
   transaction with the row lock, so a start/retry cannot interleave.
   Alternative: allow deleting any status and cancel first; rejected, a worker
   could still write results mid-delete.

6. **Detail enrichment in `serialize_obj`.** The base class adds read-only
   computed keys only when `list_view` is false (list payload stays lean),
   computed with aggregate queries in the same scoped session, so related
   rows follow visibility automatically (an invisible parent gives no value).
   Counts: results by status per experiment, test cases per dataset, users per
   organization, unrevoked unexpired refresh tokens per user (count only, via
   a repository method). fastadmin builds forms from model columns, so the keys are
   injected as synthetic read-only form fields (disabled, never writable).
   Refresh tokens counted are unrevoked, unexpired and unused.

## Risks / Trade-offs

- [Panel can now change data] → Whitelisted payload keys, per-role gates,
  tests for every refusal path, and spec scenarios for crafted requests.
- [Synthetic `password` input] → Never serialized, tested; documented.
- [Irreversible experiment delete] → Terminal states only, admin/superadmin
  only, called out in docs; no soft delete (would need a migration).
- [N+1 in detail views] → One aggregate query per detail request; lists are
  untouched.
- [Panel session of a blocked user] → Already ends on the next request
  (`get_obj` checks `is_active`).

## Migration Plan

No schema change. Deploy with the code; rollback is a plain revert (deleted
experiments are not restored, so announce before first use).
