## Access control and ownership

[Back to the README](../README.md)

**Breaking change:** all dataset, experiment, evaluation, metrics and export
endpoints now require `Authorization: Bearer <access_token>`. Without a valid
token they answer 401 (`WWW-Authenticate: Bearer`). Only the health endpoints
and `/auth/login|refresh|logout|validate` are public. Existing clients and
scripts must sign in first.

Datasets and experiments record who created them (`owner_id`) and the
organization of the creator (`organization_id`); both are in every dataset
and experiment response and are set by the server from the token. They are
never read from a request body: sending `owner_id` or `organization_id` when
creating a dataset or experiment has no effect.

| role | sees and changes |
| --- | --- |
| `staff` | only the datasets and experiments they own |
| `admin` | everything in their own organization, whoever owns it |
| `superadmin` | everything in every organization |

- Existence is hidden: a dataset, experiment or any of its sub-resources
  (cases, results, evaluations, metrics, export) outside the caller's scope
  answers `404`, with the same body as an id that does not exist. A list
  simply omits them. This also covers `start`, `cancel`, `retry-failed`,
  `evaluate` and adding cases: nothing is changed or enqueued for an
  out-of-scope experiment.
- An experiment can only be created from a dataset the caller can see (else
  `404 Dataset not found`).
- A `superadmin` has no organization, so cannot create datasets or
  experiments (`403`); they can read and manage what exists.
- Legacy data: datasets and experiments that existed before this change were
  assigned to an organization named `Default` (created if missing) with no
  owner. Its admins and all superadmins can see them; staff cannot, because
  nobody owns them.
- Background workers act on stored ids and are not scoped; scoping applies to
  the HTTP API.

### List filters

`GET /datasets` and `GET /experiments` accept, besides `limit` and `offset`:

| parameter | meaning |
| --- | --- |
| `created_from`, `created_to` | inclusive bounds on the creation time; timezone-aware ISO 8601 (a naive value or `created_from` after `created_to` is `422`); encode the `+` of an offset as `%2B` |
| `status` | experiments only: `pending`, `running`, `completed`, `partially_failed`, `failed` or `cancelled` |
| `owner_id` | only items owned by that user; useful for `admin` and `superadmin` (for `staff` it can only narrow their own items, and another user's id gives an empty list) |

Filters combine with each other and with the role's scope, and `total`
reflects the filtered set.

```bash
B=localhost:8000/api/v1
TOKEN=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
    -d '{"email": "staff@acme.test", "password": "<password>"}' \
    | python -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')

# datasets created in 2026 (UTC), then with an explicit offset
curl -H "Authorization: Bearer $TOKEN" \
    "$B/datasets?created_from=2026-01-01T00:00:00Z&created_to=2026-12-31T23:59:59Z"
curl -H "Authorization: Bearer $TOKEN" \
    "$B/datasets?created_from=2026-01-01T00:00:00%2B02:00"

# failed experiments of one user (as an admin or superadmin)
curl -H "Authorization: Bearer $TOKEN" \
    "$B/experiments?status=failed&owner_id=<user_id>&limit=20"
```
