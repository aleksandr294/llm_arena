## Authentication and users

[Back to the README](../README.md)

Users belong to organizations and sign in with email and password. A login
returns a short-lived access token (JWT) and a refresh token; both are
signed with `JWT_SECRET_KEY` (HS256).

### Roles

| role | organization | can do |
| --- | --- | --- |
| `superadmin` | none | everything: manage organizations, create and manage `admin` and `staff` users in any organization. Created only with the CLI below |
| `admin` | exactly one | create and manage `staff` and `admin` users in their own organization |
| `staff` | exactly one | sign in and read their own user record |

Role and active state are read from the database on every request, so a
role change or deactivation applies at once (a deactivated user or
organization cannot sign in, refresh, or use an existing access token).
Deactivating a user or changing their password revokes all their refresh
tokens at once, so no new access token can be obtained. Access tokens that
were already issued stay valid for their remaining lifetime (up to 15
minutes by default) after a password change or logout; only deactivating the
user or their organization stops them immediately, because they are
re-checked against the database.
Superadmins cannot be created, edited or deactivated over HTTP.

### Settings

| setting | default | meaning |
| --- | --- | --- |
| `JWT_SECRET_KEY` | none, required | signing key, at least 32 characters; the value from `.env.example` is rejected at startup |
| `JWT_ACCESS_TTL_SECONDS` | 900 (15 minutes) | access token lifetime |
| `JWT_REFRESH_TTL_SECONDS` | 604800 (7 days) | refresh token lifetime |
| `JWT_ISSUER` | `llm-arena` | `iss` claim, checked on every token |

Generate a key and never commit it:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Changing `JWT_SECRET_KEY` invalidates every issued token (all users must
sign in again).

### Create the first superadmin

There is no HTTP endpoint for this. Run the CLI on a migrated database. The
password is never an argument: it comes from `SUPERADMIN_PASSWORD` or from a
hidden prompt (asked twice). The command prints only the new user id.

```bash
python cli.py create-superadmin --email root@example.com        # prompts
SUPERADMIN_PASSWORD='...' python cli.py create-superadmin --email root@example.com
```

Emails are compared ignoring case; a duplicate or a weak password (policy:
at least 12 characters) is refused with exit code 1.

### Sign in, refresh, sign out

```bash
B=localhost:8000/api/v1

# login -> {access_token, refresh_token, token_type: "bearer", expires_in}
curl -X POST $B/auth/login -H 'Content-Type: application/json' \
    -d '{"email": "root@example.com", "password": "<password>"}'

# call a protected endpoint
curl $B/organizations -H "Authorization: Bearer <access_token>"

# validate any access token: always 200, {"valid": true, user_id,
# organization_id, role, expires_at} or {"valid": false, "reason": ...}
curl -X POST $B/auth/validate -H 'Content-Type: application/json' \
    -d '{"token": "<access_token>"}'

# refresh: returns a new pair and retires the old refresh token
curl -X POST $B/auth/refresh -H 'Content-Type: application/json' \
    -d '{"refresh_token": "<refresh_token>"}'

# logout (204): ends the whole login (the token's family)
curl -X POST $B/auth/logout -H 'Content-Type: application/json' \
    -d '{"refresh_token": "<refresh_token>"}'
```

Refresh tokens are single use. Presenting a used one again is treated as
theft: the whole login is revoked and the call returns 401, so the newest
token of that login stops working too. There is no grace window by design:
a legitimate client that retries a refresh after losing the response looks
like a reuse and loses the whole login session, so it must sign in again.
Logout accepts a correctly signed refresh token even if it has expired. Wrong credentials, unknown email,
inactive user or organization all give the same 401. A missing, malformed,
expired or revoked access token gives 401 with a `WWW-Authenticate: Bearer`
header; a valid token without the needed role gives 403. Do not use the
`validate` endpoint to learn why a login failed: its `reason` is for token
holders only.

### Organizations (superadmin only)

```bash
H="Authorization: Bearer <access_token>"
J='Content-Type: application/json'

curl -X POST $B/organizations -H "$H" -H "$J" -d '{"name": "Acme"}'   # 201
curl "$B/organizations?limit=10&offset=0" -H "$H"
curl $B/organizations/<organization_id> -H "$H"
curl -X PATCH $B/organizations/<organization_id> -H "$H" -H "$J" \
    -d '{"name": "Acme Inc", "is_active": true}'
```

Names are unique ignoring case (409 on a duplicate). Deactivating an
organization blocks its users from signing in.

### Users

```bash
# superadmin creates an admin (organization_id required)
curl -X POST $B/users -H "$H" -H "$J" \
    -d '{"email": "admin@acme.test", "password": "<password>",
         "role": "admin", "organization_id": "<organization_id>"}'

# an admin creates staff in their own organization (organization_id is
# optional and must match theirs)
curl -X POST $B/users -H "$H" -H "$J" \
    -d '{"email": "staff@acme.test", "password": "<password>", "role": "staff"}'

# list (limit 1-100; organization_id, role, is_active filters), get, update
curl "$B/users?role=staff&is_active=true" -H "$H"
curl $B/users/<user_id> -H "$H"
curl -X PATCH $B/users/<user_id> -H "$H" -H "$J" \
    -d '{"is_active": false}'    # also role (staff|admin) and password
```

Admins see and change only users of their own organization (others look
like 404); staff see only themselves. You cannot change your own role or
deactivate yourself. Responses never contain password hashes.

### Current scope and limitations

- Login has no rate limiting or lockout, per account or per IP, so put the
  API behind a proxy that limits `/auth/login` and `/auth/refresh` if it is
  exposed. Only the number of concurrent password hashes is bounded (4 per
  process), which protects memory but does not stop guessing.
- Emails are limited to printable ASCII (no spaces or non-ASCII letters, at
  most 254 characters) so that case-insensitive uniqueness is predictable.
  NUL characters are rejected in emails and organization names (422).
- Access tokens are stateless for their short lifetime but are re-checked
  against the database, so there is no separate access-token revocation
  list.
