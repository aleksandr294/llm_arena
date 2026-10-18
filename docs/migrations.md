## Migrations

[Back to the README](../README.md)

```bash
alembic upgrade head      # apply
alembic current           # show current revision
alembic revision --autogenerate -m "message"   # review before applying
```

Authentication adds revision `0005` (tables `organizations`, `users`,
`refresh_tokens`): run `alembic upgrade head` before starting the API with
this change, and create the first superadmin afterwards. Roll back with
`alembic downgrade 0004` (this deletes users, organizations and sessions).

Resource ownership adds revision `0006` (`organization_id` and `owner_id` on
`datasets` and `experiments`). Run `alembic upgrade head` before starting the
API with this change. Existing rows are assigned to the `Default`
organization with a null owner (see Access control and ownership), so no
result is lost. `alembic downgrade 0005` drops the ownership columns (the
`Default` organization stays unless it has no users, see below). If an
organization named `Default` already exists it is reused, including when it
is inactive; legacy data is then adopted by an inactive organization and
needs reactivation. The downgrade removes the `Default` organization only
if it has no users, even if the operator created it before the upgrade.
Deploy order: migrate, then restart the API
and worker; clients must send a token from then on.

Register each component's `models` module in `migrations/env.py` so
autogenerate sees its tables. The baseline revision `0001` is empty.
