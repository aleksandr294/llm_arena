# Design

## Context

Greenfield repository: only a placeholder `main.py` and a `.venv`. The layout and stack are fixed by `CLAUDE.md`; see proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- A runnable API, worker, and migration pipeline with one command each.
- Extension points (`BaseRepository`, router registration, component `tasks.py`) so later components add files, not infrastructure.

**Non-Goals:**
- Domain models, tables, LLM providers, authentication.
- Production deployment or CI pipeline configuration.

## Decisions

- **Config via `pydantic-settings`** in `components/core/config.py`, secrets as `SecretStr`. Alternative: plain `os.environ` — rejected for lack of validation. It ships with the Pydantic ecosystem already in use; it is a small justified dependency.
- **Async SQLAlchemy 2.x + `asyncpg`** with an `async_sessionmaker`; a FastAPI dependency yields a session per request and commits or rolls back. Alternative: sync psycopg — rejected because FastAPI and provider calls are I/O bound. Services own transaction boundaries (per `CLAUDE.md`); repositories only use the session passed in.
- **`BaseRepository[Model]`** offers generic get/add/delete/list; component repositories add domain queries.
- **Alembic async env** in `migrations/`, reading the URL from settings and importing component models so metadata is complete. Baseline has no tables, so first domain change generates the first revision.
- **Taskiq with `taskiq-aio-pika`** broker defined in `taskiq_app.py`; components expose tasks in `tasks.py` and the worker is started with `taskiq worker taskiq_app:broker components.*.tasks`. Result backend deferred: persisted DB state is the source of truth.
- **`main.py` CLI** dispatches `API` to uvicorn running an app factory in `api/`. Alternative: `uvicorn` invoked directly — kept as the Docker command too.
- **Readiness** performs `SELECT 1` and a broker connect with short timeouts so it cannot hang.
- **Tooling**: Black (80 cols), Ruff, mypy strict on `components` and `api`, pre-commit running all three; tests in `tests/` per component with root `conftest.py`. Integration tests use the Compose PostgreSQL/RabbitMQ.
- **Docker**: one image, Compose services `api`, `worker`, `postgres`, `rabbitmq` with healthchecks; credentials from `.env`.

## Risks / Trade-offs

- [Integration tests need running infra] → Separate pytest marker; unit tests run without services.
- [Python 3.12+ required but `.venv` is 3.13] → Poetry constraint `>=3.12,<4.0`; verify tooling works on 3.13.
- [Empty baseline migration may confuse] → Document in README/task that the first domain change creates the first revision.
- [Extra deps (`pydantic-settings`, `asyncpg`, `taskiq-aio-pika`, `uvicorn`)] → Each is required by the named stack.
