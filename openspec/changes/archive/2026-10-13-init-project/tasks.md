# Tasks

## 1. Project scaffolding

- [x] 1.1 Create `pyproject.toml` (Poetry, Python `>=3.12,<4.0`) with FastAPI, Pydantic, pydantic-settings, SQLAlchemy, asyncpg, Alembic, Taskiq, taskiq-aio-pika, uvicorn, and dev deps pytest, pytest-asyncio, httpx, Ruff, Black, mypy; verify `poetry install` succeeds
- [x] 1.2 Configure Black (80 cols), Ruff, mypy, and `.pre-commit-config.yaml`, add `.gitignore` (incl. `.env`); verify `pre-commit run --all-files` passes
- [x] 1.3 Create directory layout (`api/v1/endpoints`, `components/core`, `tests/`, `migrations/`) with only needed files; verify imports resolve with `python -c "import api, components.core"`

## 2. Configuration and API runtime

- [x] 2.1 Implement typed settings in `components/core/config.py` with `SecretStr` secrets and `.env.example`; add unit tests for missing-setting failure and secret masking, and verify they pass
- [x] 2.2 Implement the FastAPI app factory, `/api/v1` router, and liveness endpoint; add API tests (liveness 200, `/api/v2` 404) and verify they pass
- [x] 2.3 Replace placeholder `main.py` with the `API` command running uvicorn; verify `python main.py API` serves liveness locally

## 3. Database persistence

- [x] 3.1 Implement declarative base, async engine/session factory, commit/rollback session dependency, and `BaseRepository` in `components/core`; add integration tests for commit, rollback, and CRUD using a test model, and verify they pass against PostgreSQL
- [x] 3.2 Set up Alembic async env in `migrations/` reading settings and the shared metadata; verify `alembic upgrade head` succeeds on an empty database and `alembic current` reports head
- [x] 3.3 Add the readiness endpoint with timed-out PostgreSQL and RabbitMQ checks; add tests for 200 (all healthy) and 503 (database down) and verify they pass

## 4. Task runtime

- [x] 4.1 Implement the Taskiq broker in `taskiq_app.py` with component `tasks.py` discovery; add a test (in-memory broker) that a component task is registered and executes, and verify it passes
- [x] 4.2 Document and verify worker startup against RabbitMQ (`taskiq worker ...`), including the error shown when the broker is unreachable

## 5. Containers and integration

- [x] 5.1 Add Dockerfile and `docker-compose.yml` (api, worker, postgres, rabbitmq with healthchecks); verify `docker compose up` yields a healthy stack where `/api/v1/health/ready` returns 200
- [x] 5.2 Add a short README section for setup, running API/worker/migrations, and quality commands; verify each documented command runs as written
- [x] 5.3 Run the full suite: `pytest`, `ruff check`, `black --check`, `mypy`; verify all pass

## Workflow follow-up

- Request independent code review, address valid findings, then open a pull request.
- Archive the change after review requirements are satisfied.
