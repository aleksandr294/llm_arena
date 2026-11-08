# Design

## Context

`docker-compose.yml` defines `x-app-environment` with the secrets and a few
tunables; unset optionals such as `ADMIN_SESSION_COOKIE_SECURE` and
`ADMIN_SESSION_TTL_SECONDS` have no default, so Compose passes an empty string
and pydantic rejects it. `PRICING_FILE`, `LLM_MAX_CONCURRENCY`,
`API_DOCS_ENABLED`, `JWT_*_TTL_SECONDS`, `JWT_ISSUER` and
`HEALTH_CHECK_TIMEOUT_SECONDS` are not passed at all. PostgreSQL and RabbitMQ
have no volumes. The worker has no healthcheck although it serves
`/metrics` on `WORKER_METRICS_PORT`. The Dockerfile runs `pip install poetry`
unpinned from `python:3.12-slim`; CI pins `POETRY_VERSION=2.5.1`. Six runtime
and several dev dependencies use `*`. Migrations `0006` and `0007` have tests;
`migrations/env.py` has no `compare_type`; `alembic.ini` has logging sections
that `env.py` never loads. See proposal.md for motivation.

Branch `feat/tech-debt-deploy-and-migrations`, worktree
`../llm-arena-tech-debt-deploy`. Merges **after** `tech-debt-experiment-pipeline`
(migration `0009`) and `tech-debt-test-infrastructure` (shared migration test
helpers).

## Goals / Non-Goals

**Goals:**
- `docker compose up --build` works from `.env.example` plus real secrets and
  survives `down`/`up`.
- Schema, migrations and their docs agree and are tested end to end.

**Non-Goals:**
- No volumes for Loki, Prometheus and Grafana (dev observability stack).
- No automatic migration on container start (README states the operator runs
  `alembic upgrade head`; unchanged).
- No image digests: tags that name the patch version are the pin; digests are
  recorded as a documented follow-up in `docs/security.md`.

## Decisions

1. **Compose environment.** Required: `${POSTGRES_USER:?...}`,
   `${POSTGRES_PASSWORD:?...}`, `${POSTGRES_DB:?...}`,
   `${RABBITMQ_DEFAULT_USER:?...}`, `${RABBITMQ_DEFAULT_PASS:?...}`,
   `${JWT_SECRET_KEY:?...}`. Optional with code defaults: `ADMIN_SECRET_KEY:-`,
   `ADMIN_SESSION_COOKIE_SECURE:-true`, `ADMIN_SESSION_TTL_SECONDS:-28800`,
   `OPENAI_API_KEY:-`, `ANTHROPIC_API_KEY:-`, `LLM_MAX_CONCURRENCY:-4`,
   `PRICING_FILE:-`, `JWT_ACCESS_TTL_SECONDS:-900`,
   `JWT_REFRESH_TTL_SECONDS:-604800`, `JWT_ISSUER:-llm-arena`,
   `HEALTH_CHECK_TIMEOUT_SECONDS:-2.0`, `API_DOCS_ENABLED:-true`.
   Defaults equal the application defaults at merge time. Deviation: this
   plan first said `API_DOCS_ENABLED:-false`; it is `true` because that is
   the current application default (the spec requires equal defaults), and
   the compose default flips to `false` when `tech-debt-core-api-cleanups`
   merges. `RABBITMQ_ERLANG_COOKIE:-llm-arena-local-erlang-cookie` is also
   passed (not a setting of the application): a fixed non-secret internal
   cookie, independent of the broker password, because the cookie persists
   in the `rabbitmq-data` volume and the image refuses a changed value; the
   rabbitmq healthcheck runs as the `rabbitmq` user so the CLI does not
   create a root-owned cookie first.
   Alternative: `env_file: .env`, rejected: `.env` also holds
   host-oriented `DATABASE_URL`/`RABBITMQ_URL` (localhost) that must not leak
   into the containers, and `${VAR:?}` guards need explicit entries. A test
   asserts every field of `SettingsSchema` is either passed in the compose
   environment or listed in an explicit "not passed" set (host-only values
   such as `API_HOST` handled per service), so a new setting cannot be
   forgotten again.
   Caveat: Compose evaluates `:?` for the whole file, so even `docker compose
   up postgres rabbitmq` needs the required variables; `.env.example` already
   defines all of them.
2. **PRICING_FILE inside the container.** The path is the container path;
   the README documents mounting the file read-only (a `volumes:` line the
   operator adds in an override file). No default mount: no file ships.
3. **Named volumes** `postgres-data:/var/lib/postgresql/data` and
   `rabbitmq-data:/var/lib/rabbitmq`. The `postgres:17` tag keeps the data
   directory layout stable (the layout changed in 18).
4. **Worker healthcheck:** `python -c "import urllib.request as u;
   u.urlopen('http://localhost:${WORKER_METRICS_PORT:-9100}/metrics')"`,
   same style as the api check; the worker binds `0.0.0.0` inside the
   container so `localhost` works.
5. **Pins.** `ARG POETRY_VERSION=2.5.1` (kept equal to `ci.yml` by a test that
   compares the two), `python:3.12.<patch>-slim` where the patch is the
   current one at implementation time (looked up with `docker pull`, build
   verified), recorded in `docs/security.md`. Dependency `*` constraints
   become caret ranges from `poetry.lock` (for example `alembic = "^1.20"`);
   `poetry lock` must not change locked versions (`poetry check --lock` and a
   diff of `poetry.lock` limited to the content-hash).
6. **Migration `0010`** (`down_revision` `0007` while developing alone,
   re-parented to `0009` at merge): `ix_experiments_dataset_id`,
   `ix_experiment_results_test_case_id`; models get the same `Index(...)`
   entries so autogenerate stays clean. Downgrade drops them.
7. **Migration tests.** `tests/migrations/test_round_trip.py` runs `alembic
   upgrade head` then `downgrade base` on the scratch database (helpers from
   the test-infrastructure change), asserting no application tables remain;
   `test_single_head.py` uses `ScriptDirectory.get_heads()`; the existing
   `_drift` helper runs at head with `compare_type=True` configured in
   `migrations/env.py` (both online and offline `context.configure`). If
   turning on `compare_type` reveals real differences, fix the models or
   record the accepted ones in the test with a reason.
8. **`alembic.ini`:** remove `[loggers]`, `[handlers]`, `[formatters]` and
   their sections; verify `alembic current` output is unchanged (env.py never
   calls `fileConfig`).
9. **Docs.** `docs/migrations.md` and README say "`alembic heads`" instead
   of naming a head and describe revisions `0007`-`0010` (a table: revision,
   change, downgrade effect); `docs/testing.md` gains the Ruff S rules and
   pip-audit commands from `docs/security.md`; README compose section lists
   required and optional variables and the `down -v` warning (volumes now persist).

## Risks / Trade-offs

- [`:?` guards break `docker compose` for someone without a `.env`] ->
  intended; the message names the variable and README points to `.env.example`.
- [Index creation locks writes briefly] -> small tables at this stage; deploy
  between experiment runs (docs/migrations.md says so).
- [Caret ranges allow minor upgrades on the next `poetry lock`] -> lock file
  stays the source of truth; pip-audit in CI covers it.
- [Pinned base image patch gets stale] -> a documented follow-up (digest pin
  and refresh cadence) rather than silent drift.

## Migration Plan

Revision `0010`: two `CREATE INDEX`; downgrade `DROP INDEX`. Deploy order:
rebuild images, `alembic upgrade head`, restart api and worker. Existing
volumes: the first `up` after this change creates the named volumes empty, so
operators with data in the previous anonymous volumes must dump and restore
(or `docker volume` copy) once; this is called out in `docs/migrations.md`.
