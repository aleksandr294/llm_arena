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

## Datasets API

A dataset is a named, ordered set of test cases. Test cases are append-only:
each gets the next position (1, 2, ...) in its dataset. Content is stored and
returned exactly as supplied.

```bash
# create a dataset (201); name is trimmed, description is optional
curl -X POST localhost:8000/api/v1/datasets \
    -H 'Content-Type: application/json' \
    -d '{"name": "Capitals", "description": "Geography smoke test"}'

# list (limit 1-100, default 50; offset >= 0) and get (includes case_count)
curl 'localhost:8000/api/v1/datasets?limit=10&offset=0'
curl localhost:8000/api/v1/datasets/<dataset_id>

# add a test case (201); expected_output and metadata (object) are optional
curl -X POST localhost:8000/api/v1/datasets/<dataset_id>/cases \
    -H 'Content-Type: application/json' \
    -d '{"input": "Capital of France?", "expected_output": "Paris",
         "metadata": {"topic": "geo"}}'

# list test cases ordered by position
curl 'localhost:8000/api/v1/datasets/<dataset_id>/cases?limit=10'
```

List responses have the form `{items, total, limit, offset}`. Invalid input
returns 422 naming the field; an unknown dataset returns 404.

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

## Real-provider tests

Tests in `tests/llm/real/` call the real OpenAI and Anthropic APIs, cost
money, and are skipped by default. To run them:

```bash
RUN_REAL_PROVIDER_TESTS=1 OPENAI_API_KEY=... ANTHROPIC_API_KEY=... \
  pytest -m real_provider tests/llm/real
```

Each test is also skipped if its provider key is unset. Override the models
with `OPENAI_TEST_MODEL` / `ANTHROPIC_TEST_MODEL`.

## Development agents

Project subagents in `.claude/agents/` support the OpenSpec workflow. Run
them in this order for each task:

1. `coder` implements one task from the change's `tasks.md`.
2. `tester` adds tests and runs pytest, Ruff, Black and mypy.
3. `reviewer` does a read-only review of the diff against the change and
   `CLAUDE.md`.

The agents do not commit, push or merge; that stays with the main session.
