## Migrations

[Back to the README](../README.md)

```bash
alembic upgrade head      # apply
alembic current           # show current revision
alembic heads             # must list exactly one head (tests enforce it)
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

### Revisions since `0007`

The newest revision is whatever `alembic heads` prints; this table does not
track it. Run `alembic upgrade head` and restart the API and worker after
pulling a change that adds a revision.

| Revision | Change | Downgrade effect |
| -------- | ------ | ---------------- |
| `0007` | `experiment_results.response_truncated` (existing rows false) | Drops the flag; the stored text stays |
| `0008` | Auth hardening: `users.tokens_valid_after`, index `ix_refresh_tokens_expires_at`, table `auth_throttle` (tech-debt-auth-hardening) | Drops them; no result data is touched |
| `0009` | Index `ix_experiment_results_export_order` (tech-debt-experiment-pipeline) | Drops the index |
| `0010` | Indexes `ix_experiments_dataset_id` and `ix_experiment_results_test_case_id` | Drops the indexes; no data change |

`0008` and `0009` arrive with their own changes; a checkout without them
goes from `0007` straight to `0010`. Creating the `0010` indexes takes a
short write lock on `experiments` and `experiment_results`: apply it between
experiment runs.

### Compose volumes

The Compose stack keeps PostgreSQL and RabbitMQ data in the named volumes
`postgres-data` and `rabbitmq-data`. The first `up` after upgrading from a
version that used anonymous volumes starts with empty volumes. To keep the
old data, dump it before the upgrade (`docker compose exec postgres pg_dump
-U <user> <db> > arena.sql`), start the new stack, run `alembic upgrade head`
and restore it (`docker compose exec -T postgres psql -U <user> <db> <
arena.sql`). `docker compose down -v` deletes the named volumes.

### Tests

`tests/migrations` checks each data migration, an upgrade to head followed
by a downgrade to base, that `alembic heads` lists a single head, and that
the schema at head matches the models including column types
(`compare_type=True` in `migrations/env.py`).

Register each component's `models` module in `migrations/env.py` so
autogenerate sees its tables. The baseline revision `0001` is empty.
