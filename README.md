# LLM Arena for Developers

## Summary

LLM Arena is a backend platform for evaluating and comparing LLM providers
and models. The goal is reliable, reproducible evaluation rather than simply
showing responses side by side.

You can:

- create and manage datasets of evaluation cases;
- configure and run experiments that send the cases to several models
  (OpenAI and Anthropic today) through a worker;
- judge the stored responses with deterministic evaluators;
- compare quality, latency, token usage, estimated cost and error rates per
  model and export the results as JSON or CSV;
- keep the data separated per organization, with roles and an optional
  admin panel (read-only apart from blocking/unblocking organizations).

Results are stored case by case, so a partial failure never loses the
successful ones, and failed cases can be retried on their own. Measured
values (tokens, latency) are kept apart from estimated ones (cost), and a
metric a provider did not report is stored as unknown, not as zero.

`CLAUDE.md` holds the architecture rules and conventions that contributors
(human or agent) follow.

## Technical Stack

Versions come from `pyproject.toml`, `poetry.lock`, the `Dockerfile` and
`docker-compose.yml`.

| area | choice |
| --- | --- |
| Language | Python 3.12 or newer (`>=3.12,<4.0`); the Docker image uses `python:3.12-slim` |
| API | FastAPI, Uvicorn, Pydantic v2, pydantic-settings |
| Database | PostgreSQL 17 (`postgres:17` image), SQLAlchemy 2 (async) with asyncpg, Alembic migrations |
| Background work | Taskiq with `taskiq-aio-pika` on RabbitMQ 3.13 (`rabbitmq:3.13-management` image) |
| LLM providers | OpenAI SDK, Anthropic SDK (behind a shared provider interface) |
| Security | PyJWT (access tokens), argon2-cffi (password hashes) |
| CLI | Typer |
| Admin panel | fastadmin `~0.10` (optional, off by default) |
| Packaging | Poetry (virtualenv in `.venv`, see `poetry.toml`) |
| Quality | Ruff, Black (80 columns), mypy (strict), pre-commit, pytest with pytest-asyncio |
| Containers | Docker and Docker Compose (`docker-compose.yml`), see below for Podman |

Exact locked versions are in `poetry.lock`.

### Docker Compose or Podman

`docker-compose.yml` defines four services: `api`, `worker`, `postgres` and
`rabbitmq`.

- Docker: `docker compose ...` (verified with Docker 29 and Compose v5).
- Podman: Podman 5.7 is installed on the development machine and
  `podman compose` is a thin wrapper that runs an external compose provider;
  here it resolves to the same Docker Compose plugin (`podman compose
  version` prints the Compose version). Starting this stack with Podman
  (rootless networking, the `x-app` YAML anchors, health-check conditions)
  was **not tested**. If you try it, use `podman compose up -d postgres
  rabbitmq` in place of the `docker compose` commands below and check that
  both containers become healthy.

## How to start locally

### Prerequisites

- Python 3.12 or newer and [Poetry](https://python-poetry.org/)
- Docker with Compose (or Podman, untested, see above) for PostgreSQL and
  RabbitMQ
- optional: an OpenAI and/or Anthropic API key to run real experiments

### Setup

```bash
poetry install
cp .env.example .env          # then edit it, see below
docker compose up -d postgres rabbitmq
set -a; . ./.env; set +a      # export settings for local commands
```

Edit `.env`:

- Replace the `change-me` passwords. `DATABASE_URL`, `RABBITMQ_URL` and the
  `POSTGRES_*` / `RABBITMQ_DEFAULT_*` values used by docker compose must
  agree.
- `JWT_SECRET_KEY` is **required**: at least 32 random characters. The
  placeholder from `.env.example` is rejected at startup. Generate one with

  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(48))"
  ```

- Optional: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (a provider without a key
  is unavailable), `LLM_MAX_CONCURRENCY`, `PRICING_FILE` (see
  `docs/pricing.example.json`), `API_DOCS_ENABLED`.
- Optional: `ADMIN_SECRET_KEY` (at least 32 characters, different from
  `JWT_SECRET_KEY`) turns the admin panel on; leave it empty to keep it off.
  Set `ADMIN_SESSION_COOKIE_SECURE=false` only for plain-HTTP development.

Never commit `.env`.

### Prepare the database

```bash
alembic upgrade head
python cli.py create-superadmin --email root@example.com   # asks for a password
```

The password comes from a hidden prompt or from `SUPERADMIN_PASSWORD`; it is
never a command-line argument and is at least 12 characters.

### Run

```bash
python main.py API                       # API on API_HOST:API_PORT
taskiq worker taskiq_app:broker --fs-discover \
    --tasks-pattern "components/*/tasks.py"   # background worker
```

If RabbitMQ is unreachable the worker logs an `AMQPConnectionError` for the
broker URL (password masked) and retries every 5 seconds.

The whole stack in containers (API, worker, PostgreSQL, RabbitMQ):

```bash
docker compose up --build
```

The containers do not run migrations; apply `alembic upgrade head` against
the database first (for the compose database from the host, use the
`localhost` `DATABASE_URL` from `.env`).

### Check that it works

- `GET http://localhost:8000/api/v1/health/live` checks the process only.
- `GET http://localhost:8000/api/v1/health/ready` also checks PostgreSQL and
  RabbitMQ (503 if any is failing).
- Swagger UI at `http://localhost:8000/docs` (see
  [Interactive API documentation](docs/api-documentation.md)); sign in with
  `POST /api/v1/auth/login` using the superadmin you created.

## Commands

| task | command |
| --- | --- |
| Run the API | `python main.py API` |
| Run the worker | `taskiq worker taskiq_app:broker --fs-discover --tasks-pattern "components/*/tasks.py"` |
| Start PostgreSQL and RabbitMQ | `docker compose up -d postgres rabbitmq` |
| Start the whole stack | `docker compose up --build` |
| Stop the stack | `docker compose down` |
| Apply migrations | `alembic upgrade head` |
| Show current revision | `alembic current` |
| New migration | `alembic revision --autogenerate -m "message"` (review it before applying; register each component's `models` in `migrations/env.py`) |
| Roll back one step | `alembic downgrade -1` or to a revision such as `alembic downgrade 0005`; downgrades can delete data, see [Migrations](docs/migrations.md) |
| Create the first superadmin | `python cli.py create-superadmin --email <email>` |
| All tests | `pytest` (integration tests need `DATABASE_URL` pointing at PostgreSQL) |
| Tests without a database | `pytest -m "not integration"` |
| Lint | `ruff check .` |
| Format check | `black --check .` (use `black .` to format) |
| Types | `mypy` |
| All pre-commit hooks | `pre-commit run --all-files` |
| Real-provider tests (opt-in, cost money) | see [Testing](docs/testing.md) |
| Kan board helper | `scripts/kan.py`, see "Task tracking (Kan)" in `CLAUDE.md` |

Run `python`, `alembic`, `pytest` and the other tools inside the Poetry
environment (`poetry run ...` or an activated `.venv`). The settings must be
exported first (`set -a; . ./.env; set +a`); in particular the tests that
load the settings fail without a valid `JWT_SECRET_KEY`.

## Architecture

### Layers

Responsibilities are kept apart (details and rules in `CLAUDE.md`):

- **API layer** (`api/`): HTTP endpoints, request validation, authentication
  and response serialization. `api/v1/endpoints/` holds one module per
  resource.
- **Service layer** (`components/<name>/service.py`): business logic and the
  orchestration of datasets, experiments, evaluations and metrics.
- **Repository layer** (`components/<name>/repository.py`): persistence and
  queries, on top of `components/core/repository.py`.
- **Provider adapters** (`components/third_party/llm/providers/`): the
  OpenAI and Anthropic calls behind one interface, with normalized
  responses, token usage, errors and timeouts. `LLMService` selects the
  adapter; provider-specific behavior stays inside adapters.
- **Evaluation layer** (`components/evaluation/`): deterministic evaluators,
  separate from model generation.
- **Task layer** (`components/*/tasks.py`, `taskiq_app.py`): Taskiq tasks for
  experiment execution and evaluation.
- **Admin panel** (`admin/`): optional fastadmin views, read-only apart from
  blocking/unblocking organizations.
- **Core** (`components/core/`): settings, database and session handling,
  `BaseRepository`, access control and the cross-component exceptions, enums,
  schemas and helpers.

### Module layout

```
api/                  FastAPI app, v1 router, endpoints, Depends helpers
admin/                fastadmin panel, one package per component
components/
  core/               config, database, access, repository base, shared
                      exceptions/enums/schemas/utils
  <component>/        models, schemas, repository, service, tasks, plus
                      exceptions, enums, utils, dependencies where needed
  third_party/llm/    providers/, schemas, service, exceptions, enums
migrations/           Alembic
main.py  cli.py  taskiq_app.py  conftest.py
tests/                tests per component
docs/                 reference pages
```

Components are `auth`, `datasets`, `evaluation`, `experiments`, `metrics`,
`organizations` and `users`.

### Request flow

`endpoint -> Depends -> session -> service -> repository`

The FastAPI dependencies authenticate the caller, open one database session
for the request (`get_session`, declared with `scope="function"` so the
commit happens before the response is sent) and build the service through the
`build_*` factory of its component (`components/<name>/dependencies.py`). The
service builds its repositories from that session. The whole request is one
transaction: one commit on success, a rollback on error.

### Task flow

`Taskiq task -> session_scope -> service`

A task opens its session with `session_scope()` from
`components/core/database.py` (commit once on success, roll back on error,
always close) and passes it down in the same way. Side effects that must
follow durable data, such as publishing to the queue, are registered with
`on_commit` and run after the commit. Operations with a slow external call do
not hold a transaction open: `ExperimentRunner` splits each result into three
short operations (claim; model call with no database access; store the
outcome and finalize). Business code never calls `session.commit()`; only
`components/core/database.py` does.

### Dependency injection

Collaborators other than the database are passed through constructors (other
services, provider clients, settings values, the `enqueue` function).
Wiring happens only at the entry points: FastAPI dependencies, Taskiq tasks,
`cli.py` and the admin classes, through the `build_*` factories. There is no
DI library, and services never create sessions, settings or other services
themselves, which keeps them replaceable in tests.

### Access control and ownership

Users belong to organizations and have one of three roles: `superadmin`
(everything, no organization), `admin` (their organization) and `staff` (their
own data). Datasets and experiments carry an organization and an owner, and
every query is scoped by the caller's principal; out-of-scope rows answer
404. Role and active state are read from the database on every request. See
[Authentication and users](docs/authentication.md) and
[Access control and ownership](docs/access-control.md).

### How experiments execute

An experiment stores its configuration (prompt template, targets, evaluator
settings) and creates one result row per case and target. `start` publishes
the pending results to RabbitMQ after the data is committed; a worker handles
each result on its own.

- **Claim:** a result is claimed atomically (row lock) and gets a lease
  (1800 seconds). A result that is already finished or owned is skipped, so
  duplicate delivery is safe.
- **Lease:** a result left `running` by a crashed worker becomes reclaimable
  once its lease expires, and a worker whose lease was reclaimed cannot
  overwrite the new outcome. Calling `start` again re-enqueues pending and
  stale results.
- **Retry:** retryable provider errors (timeout, rate limit, server error)
  are retried inside the task with capped exponential backoff up to
  `max_attempts`; `retry-failed` re-runs only the failed results.
- **Partial failure:** every result is stored independently; the experiment
  becomes `completed`, `partially_failed` or `failed` when the last result
  finishes.

Details: [Experiments API](docs/experiments-api.md).

### How it scales

- API processes are stateless (no in-process session or job state), so you
  can run several behind a load balancer.
- Run as many worker processes as needed; RabbitMQ distributes the tasks.
  Each worker bounds simultaneous model requests per provider with
  `LLM_MAX_CONCURRENCY` (default 4); Taskiq's own parallelism is set with
  worker options such as `--workers` and `--max-async-tasks`.
- PostgreSQL is the single source of truth. Row locks and leases keep
  duplicate or concurrent delivery safe, and a lost message is recovered by
  calling `start` again.

Known limits:

- Concurrency is per process: there is no global rate limiter across
  workers, so total in-flight requests are roughly worker processes times
  `LLM_MAX_CONCURRENCY`.
- There is no login rate limiting (REST or admin panel); limit
  `/api/v1/auth/login` and `/admin/api/sign-in` at a proxy if the API is
  exposed.
- Swagger UI and ReDoc load assets from a CDN; set `API_DOCS_ENABLED=false`
  for production exposure.

### Configuration and secrets

All settings are environment variables read by `components/core/config.py`
(see `.env.example` for the full list). Secrets (`JWT_SECRET_KEY`,
`ADMIN_SECRET_KEY`, provider keys, database and broker passwords) come only
from the environment, are held as secret values and are never logged. Do not
commit `.env`.

## Development methodology

The workflow is defined in `CLAUDE.md`; this is the short version.

- **OpenSpec:** meaningful behavior changes go through an OpenSpec change in
  `openspec/changes/<name>/` with a proposal, design, tasks and spec deltas:
  propose, apply, then archive. The `openspec-*` skills in `.claude/skills/`
  drive each step. Documentation-only changes and refactors that do not
  change behavior use a lighter flow.
- **Branches:** every feature starts on a new branch from an up-to-date
  `main` (for example `feat/<change-name>`); never work on `main` or reuse an
  earlier feature's branch.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/)
  (the `git-conventional-commits` skill). Commits stay local; nothing is
  pushed, merged or opened as a pull request unless the owner asks.
- **Task board:** tasks live on a local Kan board (`scripts/kan.py`).
  Columns are `Open`, `In-progress`, `Review` and `Done`: move a card to
  `In-progress` when work starts, to `Review` after the independent review
  (with a comment following the `kan-review` skill), and to `Done` only after
  the pull request is merged.
- **Subagents:** `coder` implements one task, `tester` adds tests and runs
  the checks, `reviewer` reviews the diff read-only (see
  [Development agents](docs/development-agents.md)). The main session
  commits.
- **Review:** an independent review happens before a pull request, and valid
  findings are addressed.
- **Testing policy:** unit tests for domain logic and evaluators, integration
  tests for persistence and task execution, mocked provider responses for
  adapter tests, with timeouts, rate limits, malformed responses, retries,
  duplicate delivery and partial failures covered. Real-provider tests are
  separate and opt-in. A test run is only reported as passing if it was
  actually executed. There is exactly one `conftest.py`, at the repository
  root.
- **Quality gates:** `pytest`, `ruff check .`, `black --check .`, `mypy` and
  `pre-commit run --all-files` must pass.
- **Security:** no secrets in the repository or logs, imported datasets and
  model outputs are untrusted data, and provider responses are validated.

## Documentation

Reference pages (moved out of this file; content unchanged):

- [Datasets API](docs/datasets-api.md)
- [Experiments API](docs/experiments-api.md)
- [Evaluation API](docs/evaluation-api.md)
- [Metrics and comparison API](docs/metrics-api.md) (includes the pricing
  file format and the `docs/pricing.example.json` example)
- [Authentication and users](docs/authentication.md)
- [Access control and ownership](docs/access-control.md)
- [Admin panel](docs/admin-panel.md)
- [Interactive API documentation](docs/api-documentation.md)
- [Migrations](docs/migrations.md)
- [Quality checks and real-provider tests](docs/testing.md)
- [Development agents](docs/development-agents.md)
