# Tasks

## 1. Experiment deletion in the service layer

- [x] 1.1 Add a scoped delete to `ExperimentRepository` and `ExperimentService.delete(principal, id)` (terminal states only, `InvalidTransitionError` otherwise, cascade removes results and evaluations); verify with service/repository integration tests for completed, failed, cancelled, pending, running, out-of-scope and staff cases, and that the dataset survives.

## 2. Panel base and detail views

- [x] 2.1 Add the computed detail keys (results by status, test case count, user count, active refresh token count, parent names) to `serialize_obj` for non-list views and the matching repository count methods; verify with panel tests that list payloads are unchanged, details respect scope, and no token/hash value appears.
- [x] 2.2 Update `docs/admin-panel.md` for detail views; verify the described fields match the tests.

## 3. User and organization management

- [x] 3.1 Spike and implement the add form for users (initial password input, never serialized) and `UserAdmin.save_model` calling `UserService.create_user`/`update_user` with the caller principal and a whitelist of `is_active`; verify tests for admin/superadmin create, staff 403, duplicate email, weak password, superadmin role, cross-organization, crafted extra fields, self/superadmin block refused.
- [x] 3.2 Allow superadmins to create organizations in `OrganizationAdmin.save_model` via `OrganizationService.create`; verify tests for success, duplicate name, non-superadmin 403, and that edit still changes only `is_active`.
- [x] 3.3 Add the `revoke_refresh_tokens` bulk action to `UserAdmin` with `build_auth_service` wiring; verify tests that tokens stop working, user stays active, out-of-scope ids are untouched, staff gets 403.
- [x] 3.4 Hostile-input tests for the new write sinks (email, organization name, password) using the corpus in `tests/security/helpers.py`; verify logs and error bodies contain no password or row content.

## 4. Experiment deletion in the panel

- [x] 4.1 Enable `ExperimentAdmin` delete for admin/superadmin through `ExperimentService.delete`, mapping active experiments to 409 and staff to 403; verify panel tests including a crafted delete of another model still returning 403.

## 5. Documentation and checks

- [x] 5.1 Update `docs/admin-panel.md` (read-only section, new actions, irreversible delete) and `docs/security.md` if a scan produces a finding; verify the docs match behavior.
- [x] 5.2 Run pytest, Ruff, Black, mypy, Bandit (command in `docs/security.md`) and `pip-audit`; verify all pass and record any not-run check as not run.

## Workflow follow-up

- Request independent review, move Kan card LLM-34 to Review, commit with the `git-conventional-commits` skill; archive after the user merges.
