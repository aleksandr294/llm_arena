# Proposal

## Why

The repository is an empty PyCharm stub (`main.py` prints "Hi, PyCharm") with no
application code, dependency management, database, or tooling. Every later
capability (datasets, experiments, providers, evaluation) needs a shared
foundation with consistent layering, configuration, persistence, and task
infrastructure. Establishing it first keeps later changes small and consistent
with the architecture in `CLAUDE.md`.

## What Changes

- Add Poetry project (Python 3.12+) with FastAPI, Pydantic, SQLAlchemy
  (async), Alembic, Taskiq + RabbitMQ broker, pytest, Ruff, Black, mypy,
  pre-commit.
- Add the directory layout from `CLAUDE.md`: `api/v1`, `components/core`,
  `migrations/`, `taskiq_app.py`, `conftest.py`, `.env.example`.
- Add typed, environment-based configuration; secrets are never logged and
  `.env` is git-ignored.
- Add FastAPI application factory, versioned router, and liveness/readiness
  endpoints.
- Add SQLAlchemy base, session management, `BaseRepository`, and an empty
  baseline Alembic setup wired to the configured database.
- Add Taskiq broker wiring and a worker entry point, with no domain tasks yet.
- Add Dockerfile and Docker Compose (API, worker, PostgreSQL, RabbitMQ).
- Replace the placeholder `main.py` with a runnable entry point
  (`python main.py API`).
- Add baseline tests and CI-equivalent quality commands.

Out of scope: any domain component (datasets, experiments, evaluations), LLM
provider adapters, authentication, and any domain database tables.

## Capabilities

### New Capabilities
- `service-runtime`: application startup, typed configuration with secret
  handling, versioned API routing, and liveness/readiness endpoints.
- `database-persistence`: async PostgreSQL session and transaction management,
  base repository, and Alembic migration baseline.
- `task-runtime`: Taskiq broker/worker infrastructure over RabbitMQ that later
  components register tasks against.

### Modified Capabilities
<!-- None: no existing specs. -->

## Impact

- New files across the repo root, `api/`, `components/core/`, `migrations/`.
- New runtime dependencies listed above (all already named in `CLAUDE.md`).
- New local infrastructure requirements: PostgreSQL and RabbitMQ via Docker
  Compose.
- No existing behavior or data is affected.

## Assumptions

- Async SQLAlchemy with `asyncpg` is used; this driver is a justified addition
  needed for the chosen stack.
- Readiness checks PostgreSQL and RabbitMQ connectivity.
