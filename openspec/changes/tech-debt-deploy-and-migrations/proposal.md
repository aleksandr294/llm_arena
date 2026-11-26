# Proposal

## Why

Two Kan cards cover the deployment and schema hygiene gaps left by the final
quality audit (`ofvt04oeml0t`, marked PRIORITY, and `1oq69hj67huw`).
`docker compose up --build` is presented as the whole stack, but several
settings never reach the containers (`PRICING_FILE`, `LLM_MAX_CONCURRENCY`,
`API_DOCS_ENABLED`, token lifetimes), optional variables are passed as empty
strings that fail validation when unset (`ADMIN_SESSION_COOKIE_SECURE`,
`ADMIN_SESSION_TTL_SECONDS`), and PostgreSQL/RabbitMQ data is orphaned by
`down`/`up`. Separately, two foreign keys have no index, only two migrations
have tests, build inputs are unpinned, the worker has no healthcheck and
docs lag behind the migrations.

(Observed while planning: since the card was written, compose already passes
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ADMIN_SECRET_KEY` and
`MAX_STORED_RESPONSE_CHARS`; this change finishes the rest.)

## What Changes

- `docker-compose.yml`: every setting of `SettingsSchema` that an operator may
  tune is passed with `${VAR:-default}` (defaults equal the code defaults),
  required variables use `${VAR:?message}`, named volumes keep PostgreSQL and
  RabbitMQ data, the worker gets a healthcheck on its metrics port.
- Build inputs are pinned: Poetry version (same as CI), Python base image
  patch tag, `postgres`/`rabbitmq` tags already exact; no `*` dependency
  constraints in `pyproject.toml` (caret ranges from the lock).
- Migration `0010` adds indexes on `experiments.dataset_id` and
  `experiment_results.test_case_id`.
- Migration tests: upgrade-head/downgrade-base round trip, a single-head
  check, a model-vs-migration drift check; `compare_type=True` in
  `migrations/env.py`; dead logging sections removed from `alembic.ini`.
- Docs: `docs/migrations.md` and README stop hard-coding the head revision and
  describe `0007`-`0010`; `docs/testing.md` lists Bandit and pip-audit; README
  quick start documents the compose variables.

## Capabilities

### New Capabilities
- `container-deployment`: what the Compose stack must provide: settings
  reaching the containers, guarded required variables, persistent data,
  worker health, pinned build inputs.

### Modified Capabilities
- `database-persistence`: foreign-key indexes, migrations that round trip and
  have a single head.

## Impact

- Files: `docker-compose.yml`, `Dockerfile`, `pyproject.toml`/`poetry.lock`,
  `.env.example`, `alembic.ini`, `migrations/env.py`, new migration `0010`,
  `components/experiments/models.py`, `tests/migrations/*`, `tests/ci/*`,
  README, `docs/{migrations,testing,security}.md`.
- Database: migration `0010` (two indexes; no data change). Merges after
  `tech-debt-experiment-pipeline` (`0009`).
- Operators: existing `.env` files keep working; unset optional variables now
  get defaults instead of empty strings; required ones fail with a clear message.
- No new dependencies.
