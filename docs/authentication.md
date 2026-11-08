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
tokens and ends every access token issued so far, so the user must sign in
again. Logout ends the login of its refresh token and, the same way, every
access token the user was issued up to that moment, on all devices (their
other logins keep their refresh tokens, so those clients just refresh). The
mark is `users.tokens_valid_after`; `/auth/validate` reports such a token as
`{"valid": false, "reason": "revoked"}`. The comparison uses whole seconds
(a token has no finer `iat`), so a token issued in the same second as the
logout or password change survives; a login made right after one works.
Deactivating the user or their organization also stops tokens at once,
because they are re-checked against the database; deactivating an
organization also sets the mark for its users, so tokens issued before it
do not work again after reactivation.
Superadmins cannot be created, edited or deactivated over HTTP.

### Settings

| setting | default | meaning |
| --- | --- | --- |
| `JWT_SECRET_KEY` | none, required | signing key, at least 32 characters; the value from `.env.example` is rejected at startup |
| `JWT_ACCESS_TTL_SECONDS` | 900 (15 minutes) | access token lifetime |
| `JWT_REFRESH_TTL_SECONDS` | 604800 (7 days) | refresh token lifetime |
| `JWT_ISSUER` | `llm-arena` | `iss` claim, checked on every token |
| `JWT_LEEWAY_SECONDS` | 5 (0 to 60) | clock skew accepted for `exp` and `iat` |
| `AUTH_MAX_FAILED_ATTEMPTS` | 5 | attempts per account that lock sign-in |
| `AUTH_MAX_FAILED_ATTEMPTS_PER_ADDRESS` | 50 | failures per client address that lock it (higher: many users can share an address) |
| `AUTH_LOCKOUT_SECONDS` | 900 | lockout length, also the window failures are counted in |

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
token of that login stops working too. There is no grace window by design,
because one would weaken the theft detection. So a client that submits two
refreshes of the same token at once (or retries one after losing the
response) loses the login and must sign in again: clients should serialize
their refreshes and replace the stored token before using the new one.
Logout accepts a correctly signed refresh token even if it has expired. Wrong credentials, unknown email,
inactive user or organization all give the same 401. A missing, malformed,
expired or revoked access token gives 401 with a `WWW-Authenticate: Bearer`
header; a valid token without the needed role gives 403. Do not use the
`validate` endpoint to learn why a login failed: its `reason` is for token
holders only.

Refresh locks the user row before it issues a pair, so a deactivation that
commits first wins (401, no usable refresh token is left) and one that
arrives while a refresh is in flight waits for it and then revokes the new
token as well.

### Throttling

Failed attempts are counted in the database (`auth_throttle`, shared by every
API process) with SHA-256 hashes of the identifiers, never the email or the
address:

- `/auth/login`: per normalized email (trimmed, case-insensitive, known or
  not, so an unknown account locks exactly like a real one) and per client
  address.
- `/auth/refresh` and `/auth/validate`: per client address; an unusable
  refresh token counts as a failure, and so does an invalid or revoked access
  token. An expired access token does not count in `/auth/validate` (a gateway
  forwarding stale tokens is normal traffic).
- Admin panel sign-in: per username (scope `admin`); the panel gives no
  client address.

The account counter is incremented before the password is checked (and
committed before the slow hash), so a parallel burst of guesses cannot check
more passwords than `AUTH_MAX_FAILED_ATTEMPTS`; a correct login resets it. The
address counter counts failures, with its own higher limit
`AUTH_MAX_FAILED_ATTEMPTS_PER_ADDRESS`.

At the limit within `AUTH_LOCKOUT_SECONDS` the key is locked for that long and every attempt answers `429` with a `Retry-After`
header (seconds), even with the correct password. The panel answers a locked
sign-in with its usual failure. A correct login resets the account counter
only; the address counter keeps counting, so one valid login cannot clear an
attack in progress. The usual trade-off applies: an attacker who knows an
email can keep that account locked (at most `AUTH_LOCKOUT_SECONDS` after each
burst); tune the settings, and keep a limiting proxy in front for volume.
The client address is the socket peer (`request.client.host`). Behind a
reverse proxy that is the proxy, and `X-Forwarded-For` is not trusted, so
every client shares one address counter unless the proxy forwards the real
peer (for example uvicorn `--proxy-headers` with `--forwarded-allow-ips`).
`/auth/validate` is meant for token holders; a service that calls it for
many unrelated tokens from one address can lock itself with unusable ones.

Failed logins, lockouts, refresh reuse, permission denials and panel sign-in
are also written as security events (see `docs/security.md`).

### Purge old records

Refresh token records and throttle rows are not removed automatically (the
project has no scheduler). Run this periodically, for example from cron:

```bash
python cli.py purge-refresh-tokens --retention-days 7
# Deleted 12 refresh token(s) and 3 throttle row(s)
```

It deletes refresh tokens that expired more than the retention period ago
(default 7 days; live tokens and recently expired ones stay) and throttle
rows whose lock and counting window have ended.

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
deactivate yourself. Responses never contain password hashes. Updates lock
the user row, so two admins changing one user at once apply one after the
other (a role change and a password change both survive).

Accepted risks, also listed in `docs/security.md`:

- An admin can change, deactivate and reset the password of other admins of
  the same organization (only superadmins and yourself are excluded).
- Creating a user answers 409 when the email exists in any organization, so
  an admin can learn that an email is registered somewhere. Emails are
  unique across the platform by design.

### Current scope and limitations

- Throttling covers the credential endpoints only (see above); other
  endpoints have no request rate limit, so put the API behind a proxy that
  limits volume if it is exposed. The number of concurrent password hashes
  is also bounded (4 per process); a cancelled request keeps its slot until
  its hash finishes.
- Emails are limited to printable ASCII (no spaces or non-ASCII letters, at
  most 254 characters) so that case-insensitive uniqueness is predictable.
  NUL characters are rejected in emails and organization names (422).
- Access tokens are stateless for their short lifetime but are re-checked
  against the database and against `tokens_valid_after`, so there is no
  separate access-token revocation list.
