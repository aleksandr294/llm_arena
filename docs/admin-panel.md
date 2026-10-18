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

### Read-only, no export

Panel data is **read-only**: adding, editing and deleting are refused (403)
and so are file uploads, actions and the export buttons. The one exception is
blocking organizations, below. Export is disabled because fastadmin's export
builds its columns from all model fields regardless of the hidden-column
settings and would dump data in bulk. (fastadmin 0.10 does neutralize CSV
formula cells itself; that is not why it is off.) Use the REST export, which
is scoped, includes only documented columns and neutralizes formula cells
(see Export formats). Passwords cannot be changed in the panel.

### Block or unblock an organization

A superadmin opens an organization and flips `is_active` (the only editable
field; everything else is ignored by the server). The effect is the same as
a REST deactivation (`PATCH /organizations/<id>` with `is_active`): its users
cannot sign in or refresh, their refresh tokens are revoked, existing access
tokens and panel sessions stop working on their next request, and nothing is
deleted. Setting it back to active lets them sign in again with their
existing passwords. Admins and staff cannot block organizations.

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
