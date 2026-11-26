## Admin panel

[Back to the README](../README.md)

An optional, browser-based panel for looking at the platform's data, built
with [fastadmin](https://github.com/vsdudakov/fastadmin) and mounted at
`/admin`. It is **off unless `ADMIN_SECRET_KEY` is set**; while it is unset,
`/admin` answers 404 and fastadmin is not even imported.

### Enable it

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # generate
```

Put the value in `ADMIN_SECRET_KEY` (see `.env.example`) and restart the API.
It must be at least 32 characters and **must differ from `JWT_SECRET_KEY`**;
otherwise the API refuses to start. Open `http://localhost:8000/admin/`.

| setting | default | meaning |
| --- | --- | --- |
| `ADMIN_SECRET_KEY` | unset (panel off) | signs panel session cookies; at least 32 characters, different from `JWT_SECRET_KEY` |
| `ADMIN_SESSION_COOKIE_SECURE` | `true` | marks the session cookie `Secure` |
| `ADMIN_SESSION_TTL_SECONDS` | `28800` (8 hours, minimum 60) | lifetime of a panel session |

The session cookie is `HttpOnly` and always `SameSite=Lax` (pinned; the
environment cannot weaken it). Sign-out only clears the cookie in the browser
and does **not** revoke the signed session value, which stays valid until the
TTL ends (unless the user or organization is deactivated). Keep
`ADMIN_SESSION_TTL_SECONDS` short; rotating `ADMIN_SECRET_KEY` ends all
sessions at once.

The cookie is `Secure` by default, so over plain HTTP some browsers will not
store or send it and sign-in appears to do nothing. Set
`ADMIN_SESSION_COOKIE_SECURE=false` for local HTTP development only; keep
`true` behind HTTPS.

### Sign in and what you see

Sign in with the same email and password as the REST API. The same rules
apply: an inactive user or a user of a blocked organization cannot sign in,
and a session stops working at once when either becomes inactive. The panel
shows exactly the data the REST API would, nothing more:

| role | datasets, experiments, results, evaluations | users | organizations |
| --- | --- | --- | --- |
| `staff` | only their own | only themselves | none |
| `admin` | their whole organization | their organization (no superadmins) | none |
| `superadmin` | everything | everyone | all |

Out-of-scope rows are not listed, and opening one by id answers 404.

### What the panel can change, and what is read-only

Panel data is **read-only except for the managed operations below**. Every
other add, edit and delete is refused (403), and so are file uploads, other
actions and the export buttons. Each managed operation goes through the same
service as the REST API for the signed-in user, so the REST rules and scope
apply unchanged.

| operation | staff | admin | superadmin |
| --- | --- | --- | --- |
| view data (scoped as above) | own | organization | all |
| block/unblock a user | no | users of their organization | any user |
| create an `admin` or `staff` user | no | in their own organization | in any organization |
| revoke refresh tokens (bulk action) | no | users of their organization | any user |
| create an organization | no | no | yes |
| block/unblock an organization | no | no | yes |
| delete a finished experiment | no | in their organization | any |

Everything else (datasets, test cases, results, evaluations, editing any
other field, deleting any other model) stays read-only for every role.
Superadmins are never created in the panel, and a superadmin cannot be
blocked from it. Export is disabled because fastadmin's export builds its
columns from all model fields regardless of the hidden-column settings and
would dump data in bulk. (fastadmin 0.10 does neutralize CSV formula cells
itself; that is not why it is off.) Use the REST export, which is scoped,
includes only documented columns and neutralizes formula cells (see Export
formats). Passwords cannot be changed in the panel.

### Users: add, block, revoke tokens

**Add.** The add form has two form-only fields next to the model columns:

- `password`: the initial password, required. It is a password input, is
  never serialized, returned, filtered or sorted by, and error messages never
  quote it. It is not the stored hash.
- `organization_id`: the organization of the new user. A superadmin must
  give it; for an admin it is optional and defaults to their own
  organization (naming another one is refused with 403).

Email, password strength, duplicate emails and roles are validated by the
user service (duplicate email: 409; invalid values: 422). Any other key in
the request is ignored.

**Block or unblock.** Editing a user applies only `is_active`; everything
else is ignored. Deactivation ends the user's refresh tokens, as over REST. An
admin cannot block themselves (409) or users outside their organization
(404); superadmins cannot be blocked (not found for admins, 403 for
superadmins).

**Revoke refresh tokens.** The bulk action "Revoke refresh tokens" ends every
refresh token of the selected users and leaves their active state and
password alone. Ids the caller cannot see are skipped, the answer is the same
whatever was selected, and at most 500 ids are accepted per call.

### Organizations: create, block or unblock

A superadmin can add an organization; only `name` is read, a new organization
is always active, and a duplicate name answers 409.

A superadmin opens an organization and flips `is_active`. On edit the
`name` (and everything else) is **ignored by the server**; only `is_active`
applies. The effect is the same as a REST deactivation
(`PATCH /organizations/<id>` with `is_active`): its users cannot sign in or
refresh, their refresh tokens are revoked, existing access tokens and panel
sessions stop working on their next request, and nothing is deleted. Setting
it back to active lets them sign in again with their existing passwords.
Admins and staff cannot create or block organizations.

### Delete an experiment (irreversible)

Admins (own organization) and superadmins (any) can delete an experiment.
**There is no undo**: the experiment, all its results and their evaluations
are deleted, while the dataset and its test cases are kept. Staff cannot
delete.

Only finished experiments can be deleted. A `pending` or `running`
experiment is refused with 409: cancel it and wait until it has finished
first, so no worker writes results mid-delete. Before the delete runs the
panel checks the experiment and answers 404 when it does not exist or is out
of scope, and 409 when it is still active; the authoritative check is
repeated under a row lock in the service, so a race still removes nothing
active.

### Detail views

A record's detail page also shows read-only values computed when it is
opened (never in lists, never editable, ignored if sent in a change request):

| model | computed values |
| --- | --- |
| experiment | `dataset_name`, `owner_email`, `results_by_status` (result count per status) |
| experiment result | `experiment_name` (the case position is a regular column) |
| dataset | `owner_email`, `test_case_count` |
| test case | `dataset_name` |
| organization | `user_count` |
| user | `organization_name`, `active_refresh_tokens` |

They are read through the same scoped session as the record, so a related row
you may not see gives an empty value (`null`; counts only cover visible rows).
For example staff do not see other users' datasets, and only superadmins see
organizations, so `organization_name` is empty for admins. `active_refresh_tokens`
is a count of tokens that are not revoked, expired or already rotated; no
token, token hash or password hash is ever shown.

### Hidden columns

The password hash is never returned (nor the lowercase email copy). A test
case's `metadata` is not shown. Lists omit bulky text (prompts, responses,
error messages, configuration, evaluator details, test case input and
expected output); it appears only on a record's detail view. Treat that
content as untrusted.

### Dependency and supply chain

`fastadmin` is pinned to `~0.10`. It serves a prebuilt frontend bundle that
is not built or audited in this repository, which is why the panel is opt-in.
Do not enable it on a deployment where that is not acceptable, and update the
pin deliberately.

### Known limits

- No sign-in rate limiting or lockout (the same as REST login): limit
  `/admin/api/sign-in` at a proxy if the API is exposed.
- Panel sessions are a separate signed cookie, not REST JWTs; a REST logout
  does not end a panel session. Sign-out in the panel only deletes the cookie
  in the browser: the signed session value stays valid until it expires
  (`ADMIN_SESSION_TTL_SECONDS`, 8 hours by default) unless the user or
  organization is deactivated.
- Changing `ADMIN_SECRET_KEY` ends every panel session.
- The user admin validates a session by reading the user without a bound
  principal, which relies on each request running in its own task context
  (true for uvicorn). Do not call into the panel from a shared long-lived
  task; see the docstring of `UserAdmin.get_obj` in `admin/users/admin.py`.
