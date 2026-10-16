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

## Experiments API

An experiment runs every test case of a dataset against one or more model
targets using a prompt template, and stores each case/target pair as its own
result. Creating an experiment makes no model request: it freezes the case
set and the exact rendered prompt of every result (`pending`). Later dataset
edits do not change an existing experiment. Evaluation, cost and comparison
are not part of this API yet.

```bash
# create (201). {input} is required in prompt_template; {expected_output}
# is optional. Each target's provider needs its API key configured (else 422).
curl -X POST localhost:8000/api/v1/experiments \
    -H 'Content-Type: application/json' \
    -d '{"name": "capitals-v1", "dataset_id": "<dataset_id>",
         "prompt_template": "Answer briefly: {input}",
         "config": {"targets": [{"provider": "openai", "model": "<model>"},
                                {"provider": "anthropic", "model": "<model>"}],
                    "system_prompt": "Be concise.", "temperature": 0,
                    "max_tokens": 256, "timeout_seconds": 60,
                    "max_attempts": 3}}'

# list, detail (config + status_counts) and results (limit 1-100, offset,
# optional status filter: pending|running|succeeded|failed|cancelled)
curl 'localhost:8000/api/v1/experiments?limit=10'
curl localhost:8000/api/v1/experiments/<experiment_id>
curl 'localhost:8000/api/v1/experiments/<experiment_id>/results?status=failed&limit=20'

# start (202): enqueue the pending results for the worker
curl -X POST localhost:8000/api/v1/experiments/<experiment_id>/start

# cancel (200) and retry only the failed results (202)
curl -X POST localhost:8000/api/v1/experiments/<experiment_id>/cancel
curl -X POST localhost:8000/api/v1/experiments/<experiment_id>/retry-failed
```

`start` and `retry-failed` return `{"experiment": {...}, "enqueued": N}`.
`202` and `enqueued` mean the tasks were submitted, not that anything ran:
poll the experiment detail and its `status_counts`. Errors: unknown
experiment or dataset `404`; an action not allowed in the current status
`409`; invalid input `422` with `detail` entries that name the field in
`loc` (for example `["body", "config", "targets"]`).

Execution needs a running worker (see Run). Limits: at most 10 targets, at
most 10 000 results (cases times targets) per experiment, `max_attempts`
1-5, `timeout_seconds` above 0 and at most 120.

### Status model

- Result: `pending -> running -> succeeded | failed`; `pending -> cancelled`;
  `failed -> pending` on `retry-failed`.
- Experiment: `pending -> running -> completed | partially_failed | failed`;
  `pending | running -> cancelled`; `partially_failed | failed -> running`
  on `retry-failed`. `completed` and `cancelled` are final. The final status
  is written when the last result finishes: `completed` (all succeeded),
  `partially_failed` (some succeeded), `failed` (none succeeded).
- Stored metrics are measured values only (tokens as reported by the
  provider, latency measured by the adapter); a missing value is `null`, not
  zero.

### Cancellation and retry

- `cancel` marks pending results `cancelled` immediately. Results already
  running finish and are stored, then the experiment becomes `cancelled`.
  Cancelling a final experiment returns 409. A cancelled experiment keeps
  the status `cancelled` even when some of its results succeeded or failed;
  see `status_counts` for the breakdown. It cannot be
  restarted or retried.
- `retry-failed` re-runs only failed results (successful ones are untouched)
  and returns 409 unless the experiment is `partially_failed` or `failed`.
- `start` on a `running` experiment re-enqueues its still-pending results
  and its stale `running` results (running longer than the 1800 second
  lease, left by a crashed worker). This recovers from lost messages and
  crashes: call `start` again. Duplicate delivery is safe: a result is
  claimed atomically, and one that is already finished or owned is skipped.
  A worker whose lease was reclaimed cannot overwrite the new outcome.
- A retryable provider error (timeout, rate limit, server error) is retried
  inside the task with exponential backoff (capped at 30 seconds) up to
  `max_attempts`; other errors fail immediately. `error_kind` and a generic
  `error_message` are stored; provider error text, prompts and API keys are
  never logged or stored in error fields.

### Worker concurrency

`LLM_MAX_CONCURRENCY` (default 4) bounds simultaneous model requests per
provider inside one worker process. It is per process: total in-flight
requests are roughly worker processes times this value, and there is no
global limit across workers. Taskiq's own parallelism is set with worker
options, for example `taskiq worker ... --workers 2 --max-async-tasks 20`;
backoff sleeps hold a task slot but not a request slot.

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
