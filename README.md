# LLM Arena for Developers

Backend platform for evaluating and comparing LLM providers and models. See
`CLAUDE.md` for architecture and conventions.

## Setup

```bash
poetry install
cp .env.example .env          # replace placeholder credentials
docker compose up -d postgres rabbitmq
set -a; . ./.env; set +a      # export settings for local commands
```

## Run

```bash
python main.py API                       # API on API_HOST:API_PORT
taskiq worker taskiq_app:broker --fs-discover \
    --tasks-pattern "components/*/tasks.py"   # background worker
```

If RabbitMQ is unreachable the worker logs an `AMQPConnectionError` for the
broker URL (password masked) and retries every 5 seconds.

Health endpoints: `GET /api/v1/health/live` (process only) and
`GET /api/v1/health/ready` (PostgreSQL and RabbitMQ; 503 if any is failing).

The whole stack: `docker compose up --build`.

## Migrations

```bash
alembic upgrade head      # apply
alembic current           # show current revision
alembic revision --autogenerate -m "message"   # review before applying
```

Register each component's `models` module in `migrations/env.py` so
autogenerate sees its tables. The baseline revision `0001` is empty.

## Quality checks

```bash
pytest                    # integration tests need DATABASE_URL (PostgreSQL)
pytest -m "not integration"
ruff check .
black --check .
mypy
pre-commit run --all-files
```
