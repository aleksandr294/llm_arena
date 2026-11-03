# Proposal

## Why

The admin panel is read-only apart from blocking organizations, so operators
still need REST calls or SQL to block a user, create an organization or user,
revoke a user's sessions, remove a test experiment or understand how rows
relate. Kan card LLM-34 asks for these to be possible in the panel.

## What Changes

- **Users**: admins (own organization) and superadmins can block and unblock a
  user from the panel, with the same effects as the REST deactivation
  (sessions revoked, sign-in refused).
- **Users and organizations**: superadmins can create organizations; admins
  (own organization) and superadmins can create `admin` and `staff` users.
  Rules and validation are those of the REST API.
- **Refresh tokens**: admins (users of their organization) and superadmins
  (any user) can revoke all refresh tokens of selected users from the panel.
- **Experiments**: admins (own organization) and superadmins can delete an
  experiment that is in a terminal state; its results and evaluations are
  deleted with it. Pending and running experiments are refused with a clear
  error; staff cannot delete.
- **Detail views**: detail pages of experiments, experiment results, users,
  organizations, datasets and test cases show related data (parent dataset,
  organization and owner, result counts by status, test case count, number of
  active refresh tokens), never secrets or hashes.
- Everything else (edit of other fields, deletes of other models, export,
  uploads, password changes) stays refused with 403.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-panel`: the "Read-only data" rule gains explicit exceptions; new
  requirements for user blocking, creation of organizations and users,
  refresh-token revocation, experiment deletion and richer detail views.

## Impact

- `admin/`: users, organizations, experiments, datasets and result
  admin classes; `admin/core.py` base class helpers.
- `components/experiments`: a scoped delete operation in the service and
  repository (the repository currently disables `delete`).
- `components/users`, `components/auth`, `components/organizations`: reused
  as is; no new dependency and no migration expected (cascades already exist
  for results and evaluations).
- `docs/admin-panel.md`, `openspec/specs/admin-panel` and `tests/admin/`.
- Operational: deleted experiments cannot be recovered; the panel is the only
  delete path (no REST endpoint is added).
